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


def test_spacy_uses_ner_without_noun_chunks_or_numbers(monkeypatch):
    class Doc(list):
        ents = [
            SimpleNamespace(text="Google", label_="ORG"),
            SimpleNamespace(text="10", label_="CARDINAL"),
            SimpleNamespace(text="2024", label_="DATE"),
        ]
        noun_chunks = [SimpleNamespace(text="search engines")]

        def has_annotation(self, key):
            return key == "DEP"

    def nlp(text):
        return Doc([SimpleNamespace(text="engines", lemma_="engine")])

    monkeypatch.setitem(sys.modules, "spacy", SimpleNamespace(load=lambda name: nlp))
    analyzer = Analyzer()
    assert analyzer.backend == "spacy:en_core_web_sm"
    assert analyzer.tokens("engines") == ["engine"]
    assert analyzer.entity_candidates("Google search engines") == [("Google", "ORG")]


def test_entity_case_plural_normalization_and_frequency():
    pages = [
        page(
            "Buyers trust Apple. Many say apple makes phones. Batteries improve battery life.",
            1,
        ),
        page("Reviews of Apple batteries. Battery Life matters.", 2),
    ]
    entities = entity_table(pages, Analyzer(False))
    apple = next(e for e in entities if e["normalized"] == "apple")
    life = next(e for e in entities if e["normalized"] == "battery life")
    assert apple["pages_used"] == 2 and apple["total_mentions"] == 3
    assert life["pages_used"] == 2 and life["total_mentions"] == 2
    # A single capitalized word at a sentence start is not a candidate.
    assert not any(e["normalized"] == "battery" for e in entities)


def test_entities_need_page_threshold_and_drop_determiners():
    pages = [
        page("Readers ask The Lodge team. Readers visit Field Company.", 1),
        page("Readers ask The Lodge team again.", 2),
        page("Readers ask Lodge support.", 3),
    ]
    entities = entity_table(pages, Analyzer(False))
    names = {e["normalized"] for e in entities}
    assert "lodge" in names
    # Field Company appears on one page of three: below the two-page floor.
    assert "field company" not in names


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
        "pages": [{}],
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


def test_generic_terms_filtered_and_subsumed_ngrams_dropped():
    pages = [
        page("Make sure you season cast iron. Cast iron needs oil.", 1),
        page("Season cast iron well. Keep cast iron dry.", 2),
    ]
    terms = {t["term"] for t in term_table(pages, Analyzer(False), 100, 0.3)}
    assert "cast iron" in terms
    assert not {"make", "sure", "keep", "well"} & terms
    # "cast" never appears outside "cast iron", so it adds nothing.
    assert "cast" not in terms


def test_score_counts_only_shown_topics():
    brief = score_brief()
    brief["pages"] = [{}, {}, {}]
    brief["topics"] = [
        {"id": "topic-1", "label": "Battery life", "example_headings": ["Battery life"], "pages_used": 2},
        {"id": "topic-2", "label": "Underwater cases", "example_headings": ["Underwater cases"], "pages_used": 1},
    ]
    report = score_draft("Apple improves battery life today.", brief)
    assert [t["id"] for t in report["topics"]] == ["topic-1"]


def test_entities_written_mostly_lowercase_are_vocabulary():
    pages = [
        page("Cook the eggs. You cook them slowly and cook them well. Visit Lodge Foundry.", 1),
        page("Cook it again. We cook often at Lodge Foundry.", 2),
    ]
    names = {e["normalized"] for e in entity_table(pages, Analyzer(False))}
    assert "lodge foundry" in names
    assert "cook" not in names


def test_keyword_words_do_not_merge_topics_or_hide_gaps():
    pages = [
        page("Cast iron rusts when wet. Dry cast iron after washing.", 1,
             [{"level": 2, "text": "Why does cast iron rust?"}, {"level": 2, "text": "Other Posts You Might Enjoy"}]),
        page("Season cast iron with oil in the oven.", 2,
             [{"level": 2, "text": "How to season cast iron"}]),
    ]
    result = analyze(
        pages,
        [query_row("Can you use butter on cast iron?", "paa")],
        Analyzer(False),
        keyword="how to clean cast iron",
    )
    labels = [t["label"] for t in result["topics"]]
    assert "Other Posts You Might Enjoy" not in labels
    assert len(result["topics"]) == 2
    assert result["queries"][0]["gap"]
