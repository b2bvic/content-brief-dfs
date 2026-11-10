"""Validated options shared by planning and execution."""

from dataclasses import dataclass
from decimal import Decimal

DEFAULT_EXCLUDED = (
    "youtube.com",
    "youtu.be",
    "reddit.com",
    "quora.com",
    "pinterest.com",
    "amazon.com",
    "wikipedia.org",
)


class BriefError(Exception):
    """An expected failure whose message is safe to show to the operator."""


@dataclass(frozen=True)
class Options:
    keyword: str
    top_n: int = 10
    location_code: int = 2840
    language_code: str = "en"
    paa_depth: int = 0
    related_limit: int = 100
    llm_fanout: bool = False
    llm_model: str = "gpt-4.1-mini"
    llm_estimate: Decimal = Decimal("0.05")  # provider allowance, not a price guarantee
    min_page_fraction: float = 0.30
    target_words: int | None = None
    excluded_domains: tuple[str, ...] = DEFAULT_EXCLUDED
    max_cost: Decimal | None = None

    def __post_init__(self):
        if not self.keyword.strip() or len(self.keyword) > 700:
            raise BriefError("Keyword must contain 1 to 700 characters.")
        if self.llm_fanout and len(self.keyword) > 500:
            raise BriefError("LLM fan-out accepts keywords up to 500 characters.")
        if not 1 <= self.top_n <= 100:
            raise BriefError("--top-n must be between 1 and 100.")
        if not 0 <= self.paa_depth <= 4:
            raise BriefError("--paa-depth must be between 0 and 4.")
        if not 1 <= self.related_limit <= 1000:
            raise BriefError("--related-limit must be between 1 and 1000.")
        if not 0 < self.min_page_fraction <= 1:
            raise BriefError(
                "--min-page-fraction must be greater than 0 and at most 1."
            )
        if self.target_words is not None and self.target_words < 1:
            raise BriefError("--target-words must be positive.")
        if self.language_code != "en":
            raise BriefError(
                "This version supports English analysis (language_code=en)."
            )
        if self.location_code <= 0:
            raise BriefError("Location code must be positive.")
        for value in (self.llm_estimate, self.max_cost):
            if value is not None and (not value.is_finite() or value < 0):
                raise BriefError("Cost values must be finite and nonnegative.")
