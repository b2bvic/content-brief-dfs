"""Plan and execute the bounded competitor-data pipeline."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import asdict, replace
from decimal import Decimal
from urllib.parse import urljoin, urlsplit

import httpx

from .analysis import Analyzer, analyze
from .api import (
    DOCS,
    KEYWORDS,
    LLM,
    LLM_BASE,
    PAGE_PRICE,
    PAGES,
    SERP,
    ZERO,
    APIError,
    Cache,
    Client,
    CostError,
    estimate_keywords,
    estimate_serp,
    keywords_payload,
    llm_payload,
    serp_payload,
    validate_response,
)
from .config import BriefError, Options
from .parsing import (
    Page,
    llm_queries,
    merge_queries,
    organic_pages,
    parsed_page,
    public_url,
    readable_page,
    related_queries,
    serp_features,
)


def cached_api(cache: Cache, endpoint: str, payload: dict) -> dict | None:
    raw = cache.read(endpoint, payload)
    if raw is not None:
        try:
            validate_response(raw)
            return raw
        except APIError:
            pass
    return None


def call_plan(options: Options, cache: Cache) -> dict:
    """No writes, auth lookup, NLP imports, or network calls."""
    serp = cached_api(cache, SERP, serp_payload(options))
    known_pages = (
        organic_pages(serp, options.top_n, options.excluded_domains)[0]
        if serp
        else None
    )
    rows = []

    def row(endpoint, count, unit, hits=0, note=""):
        rows.append(
            {
                "endpoint": endpoint,
                "calls": count,
                "cached_calls": hits,
                "estimated_new_cost_usd": str(unit * (count - hits)),
                "estimated_per_call_usd": str(unit),
                "note": note,
            }
        )

    row(
        SERP,
        1,
        estimate_serp(options),
        int(serp is not None),
        "Includes async AI Overview, depth, operators, and a PAA expansion allowance.",
    )
    hits = 0
    if known_pages is not None:
        for page in known_pages:
            if cached_api(cache, PAGES, {"url": page["url"]}) or cache.read(
                "local_readability", {"url": page["url"]}
            ):
                hits += 1
    row(
        PAGES,
        len(known_pages) if known_pages is not None else options.top_n,
        PAGE_PRICE,
        hits,
        "At most top N eligible URLs from the returned SERP; free local fetch fallback on parse failure.",
    )
    row(
        KEYWORDS,
        1,
        estimate_keywords(options),
        int(cached_api(cache, KEYWORDS, keywords_payload(options)) is not None),
        "Base task fee plus requested result limit; actual returned item count may be lower.",
    )
    if options.llm_fanout:
        row(
            LLM,
            1,
            LLM_BASE + options.llm_estimate,
            int(cached_api(cache, LLM, llm_payload(options)) is not None),
            "Provider estimate is an allowance, not a guaranteed price or token cap.",
        )
    total = sum((Decimal(r["estimated_new_cost_usd"]) for r in rows), ZERO)
    return {
        "keyword": options.keyword,
        "calls": rows,
        "estimated_cost_usd": str(total),
        "max_cost_usd": str(options.max_cost) if options.max_cost is not None else None,
        "within_ceiling": options.max_cost is None or total <= options.max_cost,
        "assumptions": [
            "Cold-cache page count is an upper bound; exclusions can reduce the sample.",
            "PAA reserve: 4*(2**depth-1) clicks. The server determines actual clicks.",
            "Variable charges can exceed estimates. Stop after settlement if the ceiling is exceeded.",
            "No automatic retries. Task costs, including failed tasks, settle actual spending.",
        ],
    }


def check_public_destination(url: str):
    if not public_url(url):
        raise APIError("Fallback URL must be HTTP(S) without embedded credentials.")
    parts = urlsplit(url)
    try:
        addresses = socket.getaddrinfo(
            parts.hostname,
            parts.port or (443 if parts.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        if not addresses or any(
            not ipaddress.ip_address(address[4][0]).is_global for address in addresses
        ):
            raise APIError("Fallback refuses non-public network addresses.")
    except (OSError, ValueError):
        raise APIError("Fallback destination could not be validated.") from None


def fetch_readable(url: str, title: str, http: httpx.Client | None = None) -> Page:
    own_http = http is None
    http = http or httpx.Client(timeout=30, trust_env=False, follow_redirects=False)
    current = url
    try:
        for _ in range(6):
            check_public_destination(current)
            with http.stream(
                "GET",
                current,
                follow_redirects=False,
                headers={"User-Agent": "content-brief/0.1"},
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise APIError("Fallback redirect has no destination.")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if (
                    "text/html" not in content_type
                    and "application/xhtml+xml" not in content_type
                ):
                    raise APIError("Fallback response is not HTML.")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 5_000_000:
                        raise APIError("Fallback HTML exceeds 5 MB.")
                return readable_page(
                    body.decode(response.encoding or "utf-8", errors="replace"),
                    url,
                    title,
                )
        raise APIError("Fallback exceeded five redirects.")
    except httpx.HTTPError:
        raise APIError("Local HTTP fallback failed.") from None
    finally:
        if own_http:
            http.close()


def build_brief(
    options: Options,
    client: Client,
    analyzer: Analyzer | None = None,
    fallback_http: httpx.Client | None = None,
) -> dict:
    ceilings = [c for c in (options.max_cost, client.max_cost) if c is not None]
    if ceilings:
        client.max_cost = min(ceilings)
        options = replace(options, max_cost=client.max_cost)
    plan = call_plan(options, client.cache)
    if not plan["within_ceiling"]:
        raise CostError(
            f"Plan estimate ${plan['estimated_cost_usd']} exceeds --max-cost ${options.max_cost}."
        )
    raw_serp = client.call(SERP, serp_payload(options), estimate_serp(options))
    candidates, skipped = organic_pages(
        raw_serp, options.top_n, options.excluded_domains
    )
    queries, overviews = serp_features(raw_serp)
    pages, warnings = [], []
    for candidate in candidates:
        url, title = candidate["url"], candidate["title"]
        local = client.cache.read("local_readability", {"url": url})
        if local:
            try:
                page = Page(**local)
                if page.text.strip() and page.source == "local_readability":
                    pages.append(page)
                    continue
            except (TypeError, AttributeError):
                pass
        try:
            raw = client.call(PAGES, {"url": url}, PAGE_PRICE)
            pages.append(parsed_page(raw, url, title))
        except APIError as exc:
            warnings.append(
                {"url": url, "stage": "content_parsing", "reason": str(exc)}
            )
            try:
                page = fetch_readable(url, title, fallback_http)
                client.cache.write("local_readability", {"url": url}, page.to_dict())
                pages.append(page)
            except APIError as fallback_exc:
                skipped.append({"url": url, "reason": str(fallback_exc)})
    if not pages:
        raise BriefError(
            f"No usable competitor pages remain ({len(skipped)} skipped). No brief was written."
        )
    queries.extend(
        related_queries(
            client.call(KEYWORDS, keywords_payload(options), estimate_keywords(options))
        )
    )
    if options.llm_fanout:
        fanout = llm_queries(
            client.call(LLM, llm_payload(options), LLM_BASE + options.llm_estimate)
        )
        queries.extend(fanout)
        if not fanout:
            warnings.append(
                {
                    "stage": "llm_fanout",
                    "reason": "No fan_out_queries returned despite forced web search.",
                }
            )
    computed = analyze(
        pages,
        merge_queries(queries),
        analyzer or Analyzer(),
        options.target_words,
        options.min_page_fraction,
    )
    settings = asdict(options)
    settings["llm_estimate"] = str(options.llm_estimate)
    settings["max_cost"] = (
        str(options.max_cost) if options.max_cost is not None else None
    )
    return {
        "schema_version": 1,
        "keyword": options.keyword,
        "settings": settings,
        **computed,
        "ai_overviews": overviews,
        "skipped_pages": skipped,
        "warnings": warnings,
        "cost": {
            "estimated_usd": plan["estimated_cost_usd"],
            "settled_usd": str(client.spent),
            "calls": list(client.records),
        },
        "endpoint_docs": DOCS,
    }
