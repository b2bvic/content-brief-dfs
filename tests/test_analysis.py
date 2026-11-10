import builtins
import sys
from types import SimpleNamespace

import pytest

from content_brief.analysis import (
    Analyzer,
    analyze,
    entity_table,
    lemma,
    percentile,
    score_draft,
    term_table,
)
from content_brief.config import BriefError
from content_brief.parsing import Page, query_row


def page(text, index=1, headings=None):
    return Page(f"https://example.org/{index}", f"Page {index}", text, headings or [])


def test_scaled_iqr_uses_only_pages_with_term():
    pages = [
        page("battery " * n + "screen " * (100 - n), i)
        for i, n in enumerate([2, 4, 6, 0])
    ]
    rows = term_table(pages, Analyzer(False), target=1000, minimum=0.30)
    term = next(t for t in rows if t["term"] == "battery")
    # Nonzero scaled counts: 20, 40, 60. Inclusive IQR: 30 to 50.
    assert term["uses_per_page"] == [2, 4, 6, 0]
    assert term["uses_per_1000_words"] == [20, 40, 60, 0]
    assert term["pages_used"] == 3
    assert term["pages_total"] == 4
    assert term["recommended_min"] == 30
    assert term["recommended_max"] == 50


def test_default_thirty_percent_and_custom_threshold():
    pages = [page("battery" if i < 3 else "screen", i) for i in range(10)]
    assert "battery" in {
        r["term"] for r in term_table(pages, Analyzer(False), 100, 0.30)
    }
    assert "battery" not in {
        r["term"] for r in term_table(pages, Analyzer(False), 100, 0.31)
    }


def test_ngrams_respect_stop_words_and_sentence_boundaries():
    counts = Analyzer(False).terms(
        "Batteries and screens. Battery life improves. Privacy cookies newsletter."
    )
    assert counts["battery"] == 2
    assert counts["battery life"] == 1
    assert counts["battery life improve"] == 1
    assert "battery screen" not in counts
    assert "screen battery" not in counts
    assert "battery screen" not in Analyzer(False).terms("Battery, screen")
    assert not Analyzer(False).terms("All rights reserved.")
    assert not ({"privacy", "cookie", "newsletter"} & set(counts))


@pytest.mark.parametrize(
    "word,expected",
    [
        ("batteries", "battery"),
        ("phones", "phone"),
        ("running", "run"),
        ("children", "child"),
        ("business", "business"),
        ("analysis", "analysis"),
    ],
)
def test_fallback_lemmatization(word, expected):
    assert lemma(word) == expected


def test_optional_spacy_absent_has_working_fallback(monkeypatch):
    real_import = builtins.__import__

    def no_spacy(name, *args, **kwargs):
        if name == "spacy":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_spacy)
    analyzer = Analyzer()
    assert analyzer.backend == "english_heuristic"
    assert analyzer.tokens("Phones") == ["phone"]
    assert analyzer.entity_candidates("Google Analytics measures traffic.") == [
        ("Google Analytics", "HEURISTIC")
    ]


def test_optional_spacy_missing_model_falls_back(monkeypatch):
    def missing(name):
        raise OSError("model not installed")

    monkeypatch.setitem(sys.modules, "spacy", SimpleNamespace(load=missing))
    assert Analyzer().backend == "english_heuristic"


def test_spacy_uses_ner_noun_chunks_and_lemmas(monkeypatch):
    class Doc(list):
        ents = [SimpleNamespace(text="Google", label_="ORG")]
        noun_chunks = [SimpleNamespace(text="search engines")]

        def has_annotation(self, key):
            return key == "DEP"

    def nlp(text):
        return Doc([SimpleNamespace(text="engines", lemma_="engine")])

    monkeypatch.setitem(sys.modules, "spacy", SimpleNamespace(load=lambda name: nlp))
    analyzer = Analyzer()
    assert analyzer.backend == "spacy:en_core_web_sm"
    assert analyzer.tokens("engines") == ["engine"]
    assert analyzer.entity_candidates("Google search engines") == [
        ("Google", "ORG"),
        ("search engines", "NOUN_CHUNK"),
    ]


def test_entity_case_plural_normalization_and_frequency():
    pages = [
        page(
            "Apple makes phones. apple makes more phones. Batteries improve battery life.",
            1,
        ),
        page("Apple makes batteries. Battery life matters.", 2),
    ]
    entities = entity_table(pages, Analyzer(False))
    apple = next(e for e in entities if e["normalized"] == "apple")
    battery = next(e for e in entities if e["normalized"] == "battery")
    assert apple["pages_used"] == 2 and apple["total_mentions"] == 3
    assert battery["pages_used"] == 2 and battery["total_mentions"] == 4


def test_heading_clusters_and_query_gaps():
    pages = [
        page(
            "Battery life lasts for hours. Choose a large screen.",
            1,
            [{"level": 2, "text": "Battery life"}],
        ),
        page(
            "Battery life varies with usage.",
            2,
            [{"level": 3, "text": "Phone battery life"}],
        ),
    ]
    result = analyze(
        pages,
        [
            query_row("How long is battery life?", "paa"),
            query_row("Underwater photography", "related_search"),
        ],
        Analyzer(False),
    )
    assert len(result["topics"]) == 1
    assert result["topics"][0]["pages_used"] == 2
    answered, gap = result["queries"]
    assert len(answered["answering_pages"]) == 2 and not answered["gap"]
    assert gap["gap"] and not gap["cluster_ids"]
    assert result["target_word_count"]["target"] > 0


def test_word_band_median_and_override():
    pages = [page("battery " * n, i) for i, n in enumerate([100, 200, 300, 400])]
    result = analyze(pages, [], Analyzer(False), target_words=500)
    assert result["target_word_count"] == {
        "target": 500,
        "median": 250,
        "p25": 175,
        "p75": 325,
    }


def score_brief():
    return {
        "schema_version": 1,
        "analysis": {"backend": "english_heuristic"},
        "terms": [{"term": "battery", "recommended_min": 1, "recommended_max": 2}],
        "entities": [{"entity": "Apple", "normalized": "apple"}],
        "topics": [
            {
                "id": "topic-1",
                "label": "Battery life",
                "example_headings": ["Battery life"],
            }
        ],
        "target_word_count": {"target": 5, "median": 5, "p25": 5, "p75": 5},
    }


def test_score_perfect_and_misses():
    brief = score_brief()
    assert score_draft("Apple improves battery life today.", brief)["grade"] == 100
    report = score_draft("", brief)
    assert report["grade"] == 0
    assert len(report["misses"]) == 4
    assert report["word_count"] == 0


def test_score_penalizes_overuse_and_strips_markdown_urls():
    brief = score_brief()
    report = score_draft(
        "[Apple](https://example.org/battery/battery) battery battery battery battery",
        brief,
    )
    assert report["terms"][0]["uses"] == 4
    assert report["terms"][0]["credit"] == 0.5
    assert report["word_count"] == 5
    assert report["grade"] < 100


def test_score_refuses_backend_mismatch():
    brief = score_brief()
    brief["analysis"]["backend"] = "spacy:en_core_web_sm"
    with pytest.raises(BriefError, match="NLP model"):
        score_draft("battery", brief, Analyzer(False))


def test_empty_analysis_fails():
    with pytest.raises(BriefError, match="No usable"):
        analyze([], [], Analyzer(False))


def test_quantiles_single_page():
    assert percentile([12], 0.25) == 12
    assert percentile([0, 10], 0.25) == 2.5
