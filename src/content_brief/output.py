"""Render the computed brief and CSV tables without generating new claims."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .analysis import TERM_TABLE_ROWS, shown_terms, shown_topics


def cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: list[list]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows),
        "",
    ]


def render_markdown(brief: dict) -> str:
    band = brief["target_word_count"]
    n = len(brief["pages"])
    lines = [
        f"# Writer brief: {brief['keyword']}",
        "",
        f"Target: **{band['target']} words**. Competitor median: {band['median']:g}; p25-p75: {band['p25']:g}-{band['p75']:g} words.",
        "",
        f"Sample: {n} usable pages. Analysis: `{brief['analysis']['backend']}`.",
        "",
        "Ranges describe competitor usage. Apply editorial judgment; do not force every term into a draft.",
        "",
        "## Competitors",
        "",
    ]
    lines += table(
        ["Page", "Words", "Extraction"],
        [
            [f"[{p['title'] or p['url']}]({p['url']})", p["word_count"], p["source"]]
            for p in brief["pages"]
        ],
    )
    lines += [
        "## Terms",
        "",
        "Each range is the middle half of competitor usage, scaled to the target length. Only pages that use a term enter its range. A range describes the competitors; it is not a quota.",
        "",
        f"Top {min(TERM_TABLE_ROWS, len(brief['terms']))} of {len(brief['terms'])} terms. The full list is in `terms.csv`.",
        "",
    ]
    lines += table(
        ["Term (lemma)", "Pages", "Competitor range (scaled to target)", "Uses per 1,000 words by page"],
        [
            [
                t["term"],
                f"{t['pages_used']}/{n}",
                f"{t['recommended_min']}-{t['recommended_max']}",
                ", ".join(map(str, t["uses_per_1000_words"])),
            ]
            for t in shown_terms(brief)
        ],
    )
    lines += [
        "## Entities",
        "",
        "Named entities on enough competitor pages to clear the page threshold. HEURISTIC labels are capitalized phrase candidates, not verified entities.",
        "",
    ]
    lines += table(
        ["Entity", "Type", "Pages", "Mentions"],
        [
            [e["entity"], e["type"], f"{e['pages_used']}/{n}", e["total_mentions"]]
            for e in brief["entities"]
        ],
    )
    lines += [
        "## Topics",
        "",
        "Heading clusters use token overlap. Query matches are lexical evidence, not proof that a page answers a question.",
        "",
    ]
    topics = shown_topics(brief)
    if len(topics) < len(brief["topics"]):
        lines += [
            f"{len(topics)} of {len(brief['topics'])} heading clusters appear on two or more pages. All clusters are in `brief.json`.",
            "",
        ]
    for topic in topics:
        lines += [
            f"### {topic['label']} ({topic['pages_used']}/{n} pages)",
            "",
            *(f"- {heading}" for heading in topic["example_headings"]),
            "",
        ]
    lines += ["## Questions and search fan-out", ""]
    lines += table(
        ["Query", "Sources", "Volume", "Coverage"],
        [
            [
                q["query"],
                ", ".join(q["sources"]),
                q["search_volume"] if q["search_volume"] is not None else "unknown",
                "GAP"
                if q["gap"]
                else f"{len(q['answering_pages'])} page(s); "
                + ", ".join(q["cluster_ids"]),
            ]
            for q in brief["queries"]
        ],
    )
    lines += ["## AI Overview", ""]
    for overview in brief["ai_overviews"]:
        lines += [
            overview["text"],
            "",
            *(
                f"- [{r.get('title') or r['url']}]({r['url']})"
                for r in overview["references"]
            ),
            "",
        ]
    if not brief["ai_overviews"]:
        lines += ["No AI Overview returned.", ""]
    lines += [
        "## Collection notes",
        "",
        f"Estimated new cost: ${brief['cost']['estimated_usd']}. Settled this run: ${brief['cost']['settled_usd']}.",
        "",
    ]
    lines += [
        f"- Skipped: {p.get('url', 'unknown URL')} ({p['reason']})."
        for p in brief["skipped_pages"]
    ]
    lines += [f"- {w.get('url', w['stage'])}: {w['reason']}" for w in brief["warnings"]]
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: list[dict], headers: list[str]):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            values = {
                key: json.dumps(row[key], ensure_ascii=False)
                if isinstance(row[key], (list, dict))
                else row[key]
                for key in headers
            }
            # CSV exports can be opened in a spreadsheet. Neutralize formula prefixes.
            values = {
                key: "'" + value
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@"))
                else value
                for key, value in values.items()
            }
            writer.writerow(values)


def write_brief(brief: dict, directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "brief.json").write_text(
        json.dumps(brief, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (directory / "brief.md").write_text(render_markdown(brief), encoding="utf-8")
    write_csv(
        directory / "terms.csv",
        brief["terms"],
        [
            "term",
            "pages_used",
            "pages_total",
            "document_frequency",
            "total_uses",
            "uses_per_page",
            "uses_per_1000_words",
            "recommended_min",
            "recommended_max",
            "scaled_p25",
            "scaled_p75",
        ],
    )
    write_csv(
        directory / "entities.csv",
        brief["entities"],
        [
            "entity",
            "normalized",
            "type",
            "pages_used",
            "pages_total",
            "total_mentions",
            "mentions_per_page",
        ],
    )
    return [
        directory / name
        for name in ("brief.md", "brief.json", "terms.csv", "entities.csv")
    ]
