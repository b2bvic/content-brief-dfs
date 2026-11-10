"""Translate documented response shapes into a small analysis model."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .api import APIError, results


@dataclass
class Page:
    url: str
    title: str
    text: str
    headings: list[dict]
    source: str = "dataforseo"

    def to_dict(self) -> dict:
        return asdict(self)


def public_url(value: str) -> bool:
    """Accept HTTP URLs without embedded auth. Local fallback adds DNS checks."""
    try:
        parts = urlsplit(value)
        return (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and not (parts.username or parts.password)
        )
    except (ValueError, TypeError):
        return False


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, "")
    )


def excluded(url: str, domains: tuple[str, ...]) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    for domain in domains:
        domain = domain.lower().strip().removeprefix("www.").strip(".")
        if domain and (host == domain or host.endswith("." + domain)):
            return True
    return False


def organic_pages(
    raw: dict, top_n: int, domains: tuple[str, ...]
) -> tuple[list[dict], list[dict]]:
    candidates, skipped, seen = [], [], set()
    items = [
        item
        for result in results(raw)
        for item in result.get("items") or []
        if isinstance(item, dict)
    ]
    for item in sorted(
        (i for i in items if i.get("type") == "organic"),
        key=lambda i: i.get("rank_group") or 9999,
    ):
        url = item.get("url") or ""
        if not public_url(url):
            skipped.append({"reason": "invalid_url"})
            continue
        url = canonical_url(url)
        if url in seen:
            continue
        seen.add(url)
        if excluded(url, domains):
            skipped.append({"url": url, "reason": "excluded_domain"})
            continue
        if len(candidates) < top_n:
            candidates.append(
                {
                    "url": url,
                    "title": item.get("title") or "",
                    "rank": item.get("rank_group"),
                }
            )
    return candidates, skipped


def query_row(query: str, source: str, volume: int | None = None) -> dict:
    return {"query": query.strip(), "sources": [source], "search_volume": volume}


def merge_queries(rows: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for row in rows:
        key = " ".join(re.findall(r"\w+", row["query"].casefold()))
        if not key:
            continue
        if key not in merged:
            merged[key] = {**row, "sources": list(row["sources"])}
        else:
            target = merged[key]
            target["sources"] = sorted(set(target["sources"] + row["sources"]))
            if row.get("search_volume") is not None:
                target["search_volume"] = row["search_volume"]
    return list(merged.values())


def serp_features(raw: dict) -> tuple[list[dict], list[dict]]:
    queries, overviews = [], []
    for result in results(raw):
        for item in result.get("items") or []:
            kind = item.get("type")
            if kind == "people_also_ask":
                for question in item.get("items") or []:
                    if question.get("title"):
                        queries.append(query_row(question["title"], "paa"))
            elif kind == "related_searches":
                queries.extend(
                    query_row(q, "related_search")
                    for q in item.get("items") or []
                    if isinstance(q, str)
                )
            elif kind == "ai_overview":
                texts, references = [], []

                def walk(node):
                    if not isinstance(node, dict):
                        return
                    title = node.get("title")
                    text = node.get("text") or node.get("markdown")
                    if title:
                        queries.append(query_row(title, "ai_overview"))
                    elif text:
                        # Without an explicit subheading, preserve the first
                        # source sentence as a subtopic, without model rewriting.
                        sentence = re.split(r"(?<=[.!?])\s+", text.strip())[0]
                        queries.append(query_row(sentence, "ai_overview"))
                    if text:
                        texts.append(text)
                    references.extend(
                        r
                        for r in node.get("references") or []
                        if public_url(r.get("url", ""))
                    )
                    for child in node.get("items") or []:
                        walk(child)
                    for child in node.get("children") or []:
                        walk(child)

                # Top-level markdown duplicates element text; prefer the elements.
                if item.get("items"):
                    for node in item["items"]:
                        walk(node)
                elif item.get("markdown"):
                    texts.append(item["markdown"])
                references.extend(
                    r
                    for r in item.get("references") or []
                    if public_url(r.get("url", ""))
                )
                unique_refs = {r["url"]: r for r in references}
                overviews.append(
                    {
                        "text": "\n".join(dict.fromkeys(texts)),
                        "references": list(unique_refs.values()),
                    }
                )
    return queries, overviews


def related_queries(raw: dict) -> list[dict]:
    rows = []
    for result in results(raw):
        for item in result.get("items") or []:
            data = item.get("keyword_data") or {}
            if data.get("keyword"):
                rows.append(
                    query_row(
                        data["keyword"],
                        "related_keyword",
                        (data.get("keyword_info") or {}).get("search_volume"),
                    )
                )
    return rows


def llm_queries(raw: dict) -> list[dict]:
    # fan_out_queries is a result-level array, not text under message.sections.
    return [
        query_row(query, "llm_fanout")
        for result in results(raw)
        for query in result.get("fan_out_queries") or []
        if isinstance(query, str) and query.strip()
    ]


def parsed_page(raw: dict, url: str, title: str) -> Page:
    texts, headings = [], []
    for result in results(raw):
        if result.get("crawl_progress") not in (None, "finished"):
            raise APIError("Content parsing did not finish.")
        for item in result.get("items") or []:
            if item.get("status_code") != 200:
                raise APIError("Content parsing returned an unsuccessful page status.")
            content = item.get("page_content") or {}
            main_topics = content.get("main_topic") or []
            for topic in main_topics + (content.get("secondary_topic") or []):
                try:
                    level = int(str(topic.get("level", 0)).lower().removeprefix("h"))
                except ValueError:
                    level = 0
                heading = topic.get("h_title")
                if heading and level in (2, 3):
                    headings.append({"level": level, "text": heading})
                # secondary_content commonly repeats navigation/TOC. Exclude it.
                # Secondary topics can contain author bios, comments, and sidebars.
                if topic not in main_topics:
                    continue
                for paragraph in topic.get("primary_content") or []:
                    if paragraph.get("text"):
                        texts.append(paragraph["text"])
                for table in topic.get("table_content") or []:
                    for row in (table.get("header") or []) + (table.get("body") or []):
                        for cell in row.get("row_cells") or []:
                            if cell.get("text"):
                                texts.append(cell["text"])
    text = "\n\n".join(dict.fromkeys(t.strip() for t in texts if t.strip()))
    if not text:
        raise APIError("Content parsing returned no main text.")
    headings = [
        dict(row) for row in {(h["level"], h["text"]): h for h in headings}.values()
    ]
    return Page(url, title, text, headings)


def readable_page(html: str, url: str, title: str = "") -> Page:
    """A local readability extractor: semantic article/main, else paragraph density.

    Drop navigation, forms, sidebars, scripts, and link-heavy blocks before
    selecting the container with the largest amount of non-link prose.
    """
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select(
        "script,style,nav,header,footer,aside,form,noscript,svg,[hidden]"
    ):
        node.decompose()
    roots = soup.select("article,main,[role=main]")
    if not roots:
        roots = soup.find_all(["section", "div", "body"])
    if not roots:
        roots = [soup]

    def density(node):
        prose = sum(
            len(p.get_text(" ", strip=True)) for p in node.find_all(["p", "li"])
        )
        links = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
        return prose - 2 * links

    root = max(roots, key=density)
    headings = [
        {"level": int(h.name[1]), "text": h.get_text(" ", strip=True)}
        for h in root.find_all(["h2", "h3"])
    ]
    paragraphs = []
    for node in root.find_all(["p", "li", "td"]):
        if node.find_parent(["p", "li", "td"]) is not None:
            continue
        text = node.get_text(" ", strip=True)
        links = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
        if text and links <= len(text) * 0.5:
            paragraphs.append(text)
    text = "\n\n".join(dict.fromkeys(paragraphs))
    if not text:
        raise APIError("Local readability extraction returned no main text.")
    return Page(
        url,
        title or (soup.title.get_text(strip=True) if soup.title else ""),
        text,
        headings,
        "local_readability",
    )
