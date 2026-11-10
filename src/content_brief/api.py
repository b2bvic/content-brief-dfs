"""DataForSEO HTTP, request-hash caching, and task-cost accounting.

One task per POST. No automatic retries of potentially billable operations.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from .config import BriefError, Options

SERP = "/v3/serp/google/organic/live/advanced"
PAGES = "/v3/on_page/content_parsing/live"
KEYWORDS = "/v3/dataforseo_labs/google/related_keywords/live"
LLM = "/v3/ai_optimization/chat_gpt/llm_responses/live"
ENDPOINTS = (SERP, PAGES, KEYWORDS, LLM)
DOCS = {path: f"https://docs.dataforseo.com{path}/" for path in ENDPOINTS}
PAGE_PRICE = Decimal("0.00015")
LLM_BASE = Decimal("0.0006")
ZERO = Decimal(0)
OPERATOR = re.compile(
    r"(?i)\b(?:allinanchor|allintext|allintitle|allinurl|cache|define|definition|"
    r"filetype|id|inanchor|info|intext|intitle|inurl|link|site):"
)


class APIError(BriefError):
    """HTTP, API status, or response-shape failure."""


class CostError(BriefError):
    """A spending estimate or settled charge exceeds the run ceiling."""


class UnknownCostError(BriefError):
    """A billed response has no trustworthy task-cost settlement."""


def serp_payload(options: Options) -> dict:
    payload = {
        # This SERP field decodes percent escapes and treats '+' as a space.
        # Escape literal input so a keyword such as C++ keeps its meaning.
        "keyword": options.keyword.replace("%", "%25").replace("+", "%2B"),
        "location_code": options.location_code,
        "language_code": options.language_code,
        "depth": options.top_n + 10,
        "load_async_ai_overview": True,
    }
    if options.paa_depth:
        payload["people_also_ask_click_depth"] = options.paa_depth
    return payload


def keywords_payload(options: Options) -> dict:
    return {
        "keyword": options.keyword,
        "location_code": options.location_code,
        "language_code": options.language_code,
        "limit": options.related_limit,
        "depth": 1,
    }


def llm_payload(options: Options) -> dict:
    return {
        "user_prompt": options.keyword,
        "model_name": options.llm_model,
        "web_search": True,
        "force_web_search": True,
        "max_output_tokens": 1024,
    }


def estimate_serp(options: Options) -> Decimal:
    pages = (options.top_n + 10 + 9) // 10
    # Pricing page says x5 per operator. Use that conservative interpretation.
    multiplier = 5 ** len(OPERATOR.findall(options.keyword))
    # PAA depth is expansion depth, not the number of clicks. Reserve a
    # four-question binary expansion tree. Actual click count is server-side.
    clicks = 4 * (2**options.paa_depth - 1)
    return (
        Decimal("0.002") * pages * multiplier
        + Decimal("0.002")
        + Decimal("0.00015") * clicks
    )


def estimate_keywords(options: Options) -> Decimal:
    return Decimal("0.012") + Decimal("0.00012") * options.related_limit


def money(value: Any) -> Decimal:
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise APIError(
            "Response has a missing or invalid task cost; spending is unknown."
        ) from None


def validate_response(raw: Any) -> list[dict]:
    if not isinstance(raw, dict):
        raise APIError("Response must be a JSON object.")
    if raw.get("status_code") != 20000:
        raise APIError("DataForSEO request status is not 20000.")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
        raise APIError("Expected exactly one DataForSEO task.")
    for task in tasks:
        if task.get("status_code") != 20000:
            raise APIError("DataForSEO task status is not 20000.")
        money(task.get("cost"))
        if not isinstance(task.get("result"), list) or not task["result"]:
            raise APIError("DataForSEO task has no results.")
        if not all(isinstance(result, dict) for result in task["result"]):
            raise APIError("DataForSEO result must contain objects.")
    return tasks


def results(raw: dict) -> list[dict]:
    return [result for task in validate_response(raw) for result in task["result"]]


class Cache:
    """Store response bodies only. Auth and request headers never enter keys or files."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, endpoint: str, payload: dict) -> Path:
        canonical = json.dumps(
            [endpoint, payload], sort_keys=True, separators=(",", ":")
        )
        return self.root / (hashlib.sha256(canonical.encode()).hexdigest() + ".json")

    def read(self, endpoint: str, payload: dict) -> dict | None:
        try:
            raw = json.loads(self.path(endpoint, payload).read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else None
        except (OSError, ValueError):
            return None

    def write(self, endpoint: str, payload: dict, raw: dict):
        self.root.mkdir(parents=True, exist_ok=True)
        # Atomic replacement prevents partial JSON from interrupting later runs.
        with tempfile.NamedTemporaryFile(
            mode="w", dir=self.root, encoding="utf-8", delete=False
        ) as handle:
            temporary = Path(handle.name)
            try:
                json.dump(raw, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(self.path(endpoint, payload))
        finally:
            temporary.unlink(missing_ok=True)


class Client:
    def __init__(
        self,
        cache: Cache,
        max_cost: Decimal | None = None,
        http: httpx.Client | None = None,
    ):
        self.cache = cache
        self.max_cost = max_cost
        self.spent = ZERO
        self.records: list[dict] = []
        self._http = http or httpx.Client(
            timeout=180, trust_env=False, follow_redirects=False
        )
        self._owns_http = http is None

    def close(self):
        if self._owns_http:
            self._http.close()

    def _check(self, additional: Decimal):
        if self.max_cost is not None and self.spent + additional > self.max_cost:
            raise CostError(
                f"Cost ceiling exceeded: settled ${self.spent}, next estimate ${additional}, ceiling ${self.max_cost}."
            )

    @staticmethod
    def _redact(raw: dict, login: str, password: str) -> dict:
        secrets = sorted(
            {
                login,
                password,
                base64.b64encode(f"{login}:{password}".encode()).decode(),
            },
            key=len,
            reverse=True,
        )

        def clean(value):
            if isinstance(value, str):
                for secret in secrets:
                    if secret:
                        value = value.replace(secret, "[REDACTED]")
                return value
            if isinstance(value, list):
                return [clean(item) for item in value]
            if isinstance(value, dict):
                return {clean(key): clean(item) for key, item in value.items()}
            return value

        return clean(raw)

    def call(self, endpoint: str, payload: dict, estimate: Decimal) -> dict:
        if endpoint not in ENDPOINTS:
            raise APIError("Unsupported endpoint.")
        cached = self.cache.read(endpoint, payload)
        if cached is not None:
            try:
                tasks = validate_response(cached)
            except APIError:
                pass  # Corrupt or unsuccessful entries are cache misses.
            else:
                self.records.append(
                    {
                        "endpoint": endpoint,
                        "cached": True,
                        "task_cost_usd": str(
                            sum((money(t["cost"]) for t in tasks), ZERO)
                        ),
                        "charged_usd": "0",
                    }
                )
                return cached
        self._check(estimate)
        login = os.environ.get("DATAFORSEO_LOGIN", "")
        password = os.environ.get("DATAFORSEO_PASSWORD", "")
        if not login or not password:
            raise APIError(
                "Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD for uncached requests."
            )
        try:
            response = self._http.post(
                "https://api.dataforseo.com" + endpoint,
                json=[payload],
                auth=httpx.BasicAuth(login, password),
            )
            response.raise_for_status()
            raw = response.json()
        except (httpx.HTTPError, ValueError):
            raise UnknownCostError(
                "DataForSEO HTTP or JSON failure; spending is unknown. Check billing before retrying."
            ) from None
        # Settle even failed tasks when costs are available, before status handling.
        if (
            isinstance(raw, dict)
            and isinstance(raw.get("tasks"), list)
            and raw["tasks"]
        ):
            try:
                if not all(isinstance(task, dict) for task in raw["tasks"]):
                    raise APIError("Invalid task.")
                charge = sum((money(task.get("cost")) for task in raw["tasks"]), ZERO)
            except APIError:
                raise UnknownCostError(
                    "Missing or invalid task costs; stop and check billing before retrying."
                ) from None
            self.spent += charge
            self.records.append(
                {
                    "endpoint": endpoint,
                    "cached": False,
                    "task_cost_usd": str(charge),
                    "charged_usd": str(charge),
                }
            )
        else:
            raise UnknownCostError(
                "Response has no task costs; stop and check billing before retrying."
            )
        try:
            validate_response(raw)
        except APIError:
            self._check(ZERO)
            raise
        raw = self._redact(raw, login, password)
        # A successful but unexpectedly expensive task must remain replayable.
        self.cache.write(endpoint, payload, raw)
        self._check(ZERO)
        return raw
