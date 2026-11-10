"""Exercise the optional model when installed; the base suite needs no spaCy."""

import importlib.util

import pytest

from content_brief.analysis import Analyzer, analyze, score_draft
from content_brief.parsing import Page, query_row


@pytest.mark.skipif(
    importlib.util.find_spec("en_core_web_sm") is None,
    reason="optional NLP model not installed",
)
def test_real_optional_nlp_model_offline():
    analyzer = Analyzer()
    assert analyzer.backend == "spacy:en_core_web_sm"
    pages = [
        Page(
            "https://example.org/",
            "Guide",
            "Google builds search engines. Google Analytics measures traffic.",
            [{"level": 2, "text": "Search engines"}],
        )
    ]
    brief = {
        "schema_version": 1,
        **analyze(pages, [query_row("Search engines", "paa")], analyzer),
    }
    assert any(entity["type"] == "ORG" for entity in brief["entities"])
    assert not any(entity["type"] == "NOUN_CHUNK" for entity in brief["entities"])
    assert "engine" in analyzer.tokens("engines")
    report = score_draft(pages[0].text, brief, analyzer)
    assert report["grade"] == 100
