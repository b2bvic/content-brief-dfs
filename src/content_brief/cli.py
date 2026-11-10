"""The content-brief brief/score command line."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .analysis import Analyzer, score_draft
from .api import Cache, Client
from .config import DEFAULT_EXCLUDED, BriefError, Options
from .output import write_brief
from .pipeline import build_brief, call_plan


def dollars(value: str) -> Decimal:
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0:
            raise ValueError
        return result
    except (ValueError, InvalidOperation):
        raise argparse.ArgumentTypeError(
            "Expected a finite, nonnegative USD amount."
        ) from None


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="content-brief",
        description="Compute writer briefs and score drafts against competitor metrics.",
    )
    commands = root.add_subparsers(dest="command", required=True)
    brief = commands.add_parser(
        "brief", help="Collect competitor data and write a brief"
    )
    brief.add_argument("keyword")
    brief.add_argument(
        "--dry-run",
        action="store_true",
        help="Print call plan without credentials, writes, or network",
    )
    brief.add_argument("--max-cost", type=dollars)
    brief.add_argument("--top-n", type=int, default=10)
    brief.add_argument("--location-code", type=int, default=2840)
    brief.add_argument(
        "--paa-depth", type=int, default=0, help="Optional PAA click depth, 1-4"
    )
    brief.add_argument("--related-limit", type=int, default=100)
    brief.add_argument(
        "--llm-fanout",
        action="store_true",
        help="Ask the ChatGPT app from five intent angles and record its sub-searches ($0.02)",
    )
    brief.add_argument("--min-page-fraction", type=float, default=0.30)
    brief.add_argument("--target-words", type=int)
    brief.add_argument(
        "--include-excluded",
        action="store_true",
        help="Include the default non-competitor domains",
    )
    brief.add_argument(
        "--exclude-domain",
        action="append",
        default=[],
        help="Add an excluded domain; repeatable",
    )
    brief.add_argument(
        "--no-nlp",
        action="store_true",
        help="Use deterministic English heuristics even if spaCy is installed",
    )
    brief.add_argument("--cache-dir", type=Path, default=Path(".cache/content-brief"))
    brief.add_argument("--output", type=Path, default=Path("out"))
    score = commands.add_parser(
        "score", help="Score a draft against brief.json, offline"
    )
    score.add_argument("draft", type=Path)
    score.add_argument("--brief", type=Path, required=True)
    score.add_argument(
        "--json",
        action="store_true",
        help="Write the full score report to stdout as JSON",
    )
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    client = None
    try:
        if args.command == "score":
            brief = json.loads(args.brief.read_text(encoding="utf-8"))
            try:
                report = score_draft(args.draft.read_text(encoding="utf-8"), brief)
            except (KeyError, TypeError, ZeroDivisionError, AttributeError):
                raise BriefError("Invalid brief.json schema or metrics.") from None
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print(f"Grade: {report['grade']}/100\nWords: {report['word_count']}")
                for miss in report["misses"]:
                    print(f"- {miss}")
            return 0
        domains = (() if args.include_excluded else DEFAULT_EXCLUDED) + tuple(
            args.exclude_domain
        )
        options = Options(
            keyword=args.keyword,
            top_n=args.top_n,
            location_code=args.location_code,
            paa_depth=args.paa_depth,
            related_limit=args.related_limit,
            llm_fanout=args.llm_fanout,
            min_page_fraction=args.min_page_fraction,
            target_words=args.target_words,
            excluded_domains=domains,
            max_cost=args.max_cost,
        )
        cache = Cache(args.cache_dir)
        if args.dry_run:
            plan = call_plan(options, cache)
            print(json.dumps(plan, indent=2))
            return 0 if plan["within_ceiling"] else 2
        client = Client(cache, options.max_cost)
        brief = build_brief(options, client, Analyzer(use_nlp=not args.no_nlp))
        paths = write_brief(brief, args.output)
        print(f"Wrote {', '.join(map(str, paths))}. Settled cost: ${client.spent}.")
        return 0
    except (BriefError, OSError, ValueError) as exc:
        # OSError filenames and response messages may contain user data. Avoid
        # dumping raw exceptions, headers, bodies, or transport tracebacks.
        message = (
            str(exc)
            if isinstance(exc, BriefError)
            else "Could not read or write a required file, or invalid JSON."
        )
        print(f"content-brief: {message}", file=sys.stderr)
        if client is not None:
            print(
                f"Settled cost before failure: ${client.spent}. No automatic retry.",
                file=sys.stderr,
            )
        return 2
    finally:
        if client is not None:
            client.close()
