"""Deterministic term ranges, entity frequency, and lexical topic coverage."""

from __future__ import annotations

import math
import re
from collections import Counter

from .config import BriefError
from .parsing import Page

WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?|[0-9]+(?:\.[0-9]+)?")
STOP = set(
    """a about above after again against all am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had
has have having he her here hers herself him himself his how i if in into is it its itself just
me more most my myself no nor not now of off on once only or other our ours ourselves out over
own same she should so some such than that the their theirs them themselves then there these
they this those through to too under until up very was we were what when where which while who
whom why will with would you your yours yourself yourselves also may might must shall whether
via per using use used get new one two many much like""".split()
)
BOILERPLATE = set(
    """copyright cookies cookie privacy newsletter subscribe login signup navigation menu javascript
reserved rights advertisement""".split()
)
IRREGULAR = {
    "children": "child",
    "people": "person",
    "men": "man",
    "women": "woman",
    "teeth": "tooth",
    "feet": "foot",
    "mice": "mouse",
    "analyses": "analysis",
    "indices": "index",
    "running": "run",
    "ran": "run",
    "better": "good",
    "best": "good",
    "was": "be",
    "were": "be",
    "is": "be",
    "are": "be",
}


def lemma(word: str) -> str:
    word = word.casefold().replace("’", "'")
    word = word.removesuffix("'s")
    if word in IRREGULAR:
        return IRREGULAR[word]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "xes", "zes", "sses")):
        return word[:-2]
    if (
        len(word) > 3
        and word.endswith("s")
        and not word.endswith(("ss", "us", "is", "ics"))
    ):
        return word[:-1]
    if len(word) > 5 and word.endswith("ing"):
        stem = word[:-3]
        return stem[:-1] if len(stem) > 2 and stem[-1] == stem[-2] else stem
    if len(word) > 4 and word.endswith("ed"):
        stem = word[:-2]
        return stem[:-1] if len(stem) > 2 and stem[-1] == stem[-2] else stem
    return word


FILTERED = STOP | BOILERPLATE | {lemma(token) for token in STOP | BOILERPLATE}
# spaCy's lemma for "reserved" differs from the lightweight suffix heuristic.
FILTERED |= {"reserve", "advertise"}


def word_count(text: str) -> int:
    return len(WORD.findall(text))


def markdown_text(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return text


class Analyzer:
    def __init__(self, use_nlp: bool = True):
        self.nlp = None
        if use_nlp:
            try:
                import spacy

                self.nlp = spacy.load("en_core_web_sm")
            except (ImportError, OSError):
                pass
        self.backend = "spacy:en_core_web_sm" if self.nlp else "english_heuristic"

    def tokens(self, text: str) -> list[str]:
        if self.nlp:
            return [
                (token.lemma_ or token.text).casefold()
                for token in self.nlp(text)
                if WORD.fullmatch(token.text)
            ]
        return [lemma(token) for token in WORD.findall(text)]

    def content_tokens(self, text: str) -> set[str]:
        return {
            token
            for token in self.tokens(text)
            if token not in FILTERED and len(token) > 1
        }

    def terms(self, text: str) -> Counter:
        counts: Counter = Counter()
        # Keep punctuation/stop-word boundaries. Never join unrelated words after filtering.
        for sentence in re.split(r"[^A-Za-z0-9'’ \t-]+", text):
            tokens = self.tokens(sentence)
            for size in (1, 2, 3):
                for start in range(len(tokens) - size + 1):
                    phrase = tokens[start : start + size]
                    if any(t in FILTERED or len(t) < 2 or t.isdigit() for t in phrase):
                        continue
                    counts[" ".join(phrase)] += 1
        return counts

    def entity_candidates(self, text: str) -> list[tuple[str, str]]:
        if self.nlp:
            doc = self.nlp(text)
            values = [(ent.text, ent.label_) for ent in doc.ents]
            if doc.has_annotation("DEP"):
                values.extend((chunk.text, "NOUN_CHUNK") for chunk in doc.noun_chunks)
            return values
        # Capitalized phrases are candidates, not validated named entities.
        pattern = r"\b[A-Z][A-Za-z0-9'-]*(?:[ \t]+[A-Z][A-Za-z0-9'-]*){0,4}\b"
        values = []
        for match in re.finditer(pattern, text):
            parts = match.group().split()
            while parts and parts[0].casefold() in STOP:
                parts.pop(0)
            if parts and not all(p.casefold() in STOP | BOILERPLATE for p in parts):
                values.append((" ".join(parts), "HEURISTIC"))
        return values


def percentile(values: list[float], fraction: float) -> float:
    """Linear interpolation at (n-1)*fraction (inclusive quantiles)."""
    if not values:
        raise BriefError("Cannot compute percentiles without pages.")
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def count_phrase(tokens: list[str], phrase: list[str]) -> int:
    if not phrase:
        return 0
    size = len(phrase)
    return sum(tokens[i : i + size] == phrase for i in range(len(tokens) - size + 1))


def term_table(
    pages: list[Page], analyzer: Analyzer, target: int, minimum: float
) -> list[dict]:
    counts = [analyzer.terms(page.text) for page in pages]
    lengths = [word_count(page.text) for page in pages]
    terms = sorted(set().union(*(set(c) for c in counts)))
    rows = []
    for term in terms:
        frequencies = [c[term] for c in counts]
        used = sum(f > 0 for f in frequencies)
        if used / len(pages) + 1e-12 < minimum:
            continue
        rates = [f * 1000 / length for f, length in zip(frequencies, lengths)]
        scaled = [f * target / length for f, length in zip(frequencies, lengths) if f]
        low, high = percentile(scaled, 0.25), percentile(scaled, 0.75)
        rows.append(
            {
                "term": term,
                "pages_used": used,
                "pages_total": len(pages),
                "document_frequency": round(used / len(pages), 6),
                "total_uses": sum(frequencies),
                "uses_per_page": frequencies,
                "uses_per_1000_words": [round(rate, 4) for rate in rates],
                "recommended_min": max(1, math.floor(low)),
                "recommended_max": max(1, math.ceil(high)),
                "scaled_p25": round(low, 4),
                "scaled_p75": round(high, 4),
            }
        )
    return sorted(rows, key=lambda r: (-r["pages_used"], -r["total_uses"], r["term"]))


def entity_table(pages: list[Page], analyzer: Analyzer) -> list[dict]:
    candidates = {}
    for page in pages:
        for text, kind in analyzer.entity_candidates(page.text):
            normalized = " ".join(analyzer.tokens(text))
            if not normalized or all(
                token in STOP | BOILERPLATE for token in normalized.split()
            ):
                continue
            if (
                normalized not in candidates
                or candidates[normalized][1] == "NOUN_CHUNK"
            ):
                candidates[normalized] = (text, kind)
    tokens = [analyzer.tokens(page.text) for page in pages]
    rows = []
    for normalized, (label, kind) in sorted(candidates.items()):
        mentions = [count_phrase(t, normalized.split()) for t in tokens]
        if not any(mentions):
            continue
        rows.append(
            {
                "entity": label,
                "normalized": normalized,
                "type": kind,
                "pages_used": sum(m > 0 for m in mentions),
                "pages_total": len(pages),
                "total_mentions": sum(mentions),
                "mentions_per_page": mentions,
            }
        )
    return sorted(
        rows, key=lambda r: (-r["pages_used"], -r["total_mentions"], r["normalized"])
    )


def overlap(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


def topic_clusters(pages: list[Page], analyzer: Analyzer) -> list[dict]:
    groups: list[list[dict]] = []
    for page in pages:
        for heading in page.headings:
            tokens = analyzer.content_tokens(heading["text"])
            if not tokens:
                continue
            row = {**heading, "url": page.url, "tokens": tokens}
            group = next(
                (
                    group
                    for group in groups
                    if any(overlap(tokens, other["tokens"]) >= 0.4 for other in group)
                ),
                None,
            )
            if group is None:
                groups.append([row])
            else:
                group.append(row)
    clusters = []
    for index, group in enumerate(groups, 1):
        examples = list(dict.fromkeys(h["text"] for h in group))
        urls = sorted({h["url"] for h in group})
        clusters.append(
            {
                "id": f"topic-{index}",
                "label": examples[0],
                "pages_used": len(urls),
                "pages_total": len(pages),
                "pages": urls,
                "example_headings": examples,
                "tokens": sorted(set().union(*(h["tokens"] for h in group))),
            }
        )
    return sorted(clusters, key=lambda c: (-c["pages_used"], c["label"]))


def covers(query: set[str], text: set[str], threshold: float = 0.6) -> bool:
    return bool(query) and len(query & text) / len(query) >= threshold


def text_windows(text: str, analyzer: Analyzer) -> list[set[str]]:
    words = text.split()
    return [
        analyzer.content_tokens(" ".join(words[i : i + 80]))
        for i in range(0, len(words), 40)
    ]


def map_queries(
    queries: list[dict], clusters: list[dict], pages: list[Page], analyzer: Analyzer
) -> list[dict]:
    page_windows = {p.url: text_windows(p.text, analyzer) for p in pages}
    output = []
    for row in queries:
        tokens = analyzer.content_tokens(row["query"])
        matched = [
            c
            for c in clusters
            if any(
                covers(tokens, analyzer.content_tokens(h))
                for h in c["example_headings"]
            )
        ]
        urls = {url for c in matched for url in c["pages"]}
        urls.update(
            url
            for url, windows in page_windows.items()
            if any(covers(tokens, window) for window in windows)
        )
        output.append(
            {
                **row,
                "cluster_ids": [c["id"] for c in matched],
                "answering_pages": sorted(urls),
                "gap": not urls,
            }
        )
    return output


def analyze(
    pages: list[Page],
    queries: list[dict],
    analyzer: Analyzer,
    target_words: int | None = None,
    minimum: float = 0.30,
) -> dict:
    if not pages or any(word_count(p.text) == 0 for p in pages):
        raise BriefError("No usable competitor main text remains.")
    lengths = [word_count(page.text) for page in pages]
    target = target_words or max(1, round(percentile(lengths, 0.5)))
    clusters = topic_clusters(pages, analyzer)
    return {
        "analysis": {
            "backend": analyzer.backend,
            "language": "en",
            "min_page_fraction": minimum,
            "quantiles": "linear (n-1)*p; only pages using a term enter its range",
            "coverage_method": "lexical overlap heuristic; a match does not verify an answer",
        },
        "target_word_count": {
            "target": target,
            "median": percentile(lengths, 0.5),
            "p25": percentile(lengths, 0.25),
            "p75": percentile(lengths, 0.75),
        },
        "pages": [
            {**p.to_dict(), "word_count": length} for p, length in zip(pages, lengths)
        ],
        "terms": term_table(pages, analyzer, target, minimum),
        "entities": entity_table(pages, analyzer),
        "topics": clusters,
        "queries": map_queries(queries, clusters, pages, analyzer),
    }


def score_draft(draft: str, brief: dict, analyzer: Analyzer | None = None) -> dict:
    if brief.get("schema_version") != 1:
        raise BriefError("Unsupported or missing brief schema_version.")
    backend = brief["analysis"]["backend"]
    analyzer = analyzer or Analyzer(use_nlp=backend.startswith("spacy:"))
    if analyzer.backend != backend:
        raise BriefError(
            "Install the NLP model used by this brief, or regenerate the brief with --no-nlp."
        )
    draft = markdown_text(draft)
    counts = analyzer.terms(draft)
    tokens = analyzer.tokens(draft)
    terms, entities, topics, misses = [], [], [], []
    for term in brief["terms"]:
        actual = counts[term["term"]]
        low, high = term["recommended_min"], term["recommended_max"]
        credit = (
            actual / low if actual < low else high / actual if actual > high else 1.0
        )
        terms.append(
            {
                "term": term["term"],
                "uses": actual,
                "min": low,
                "max": high,
                "in_range": low <= actual <= high,
                "credit": credit,
            }
        )
        if not low <= actual <= high:
            misses.append(
                f"Term '{term['term']}': {actual} uses; expected {low}-{high}."
            )
    for entity in brief["entities"]:
        actual = count_phrase(tokens, entity["normalized"].split())
        entities.append(
            {"entity": entity["entity"], "mentions": actual, "covered": actual > 0}
        )
        if not actual:
            misses.append(f"Entity missing: {entity['entity']}.")
    windows = text_windows(draft, analyzer)
    for topic in brief["topics"]:
        covered = any(
            covers(analyzer.content_tokens(h), window)
            for h in topic["example_headings"]
            for window in windows
        )
        topics.append({"id": topic["id"], "label": topic["label"], "covered": covered})
        if not covered:
            misses.append(f"Topic missing: {topic['label']}.")
    length = word_count(draft)
    band = brief["target_word_count"]
    # An explicit target shifts the competitor band proportionally.
    scale = band["target"] / band["median"]
    low, high = band["p25"] * scale, band["p75"] * scale
    word_credit = min(1.0, length / low) if length < low else min(1.0, high / length)
    if not low <= length <= high:
        misses.append(
            f"Word count: {length}; expected {math.floor(low)}-{math.ceil(high)}."
        )
    components = {
        "terms": sum(t["credit"] for t in terms) / len(terms) if terms else None,
        "entities": sum(e["covered"] for e in entities) / len(entities)
        if entities
        else None,
        "topics": sum(t["covered"] for t in topics) / len(topics) if topics else None,
        "word_count": word_credit,
    }
    weights = {"terms": 50, "entities": 20, "topics": 20, "word_count": 10}
    available = sum(weights[k] for k, v in components.items() if v is not None)
    grade = round(
        sum(weights[k] * v for k, v in components.items() if v is not None)
        / available
        * 100
    )
    return {
        "grade": grade,
        "word_count": length,
        "word_count_band": [low, high],
        "components": components,
        "terms": terms,
        "entities": entities,
        "topics": topics,
        "misses": misses,
    }
