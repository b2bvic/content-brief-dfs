# Build validation

Date: 2026-11-10. This is a local implementation audit, not an independent promotion approval.

The external build contract calls for a DataForSEO-backed Python package that computes writer briefs, caches requests, and scores drafts. Its source path is private and is omitted from this shareable repository.

## Findings

1. Independent promotion review is pending. This session permits network access only for packages and documentation. The mandated external review wrapper also writes its receipt outside the worktree. It was inspected but not executed.
2. A live DataForSEO smoke run remains unverified. No live API request or credential-file read occurred. All transport evidence comes from documented-response fixtures and mocks.
3. Variable server-side PAA expansion and LLM charges prevent a guaranteed in-flight spending cap. The planner states its assumptions. The client settles task costs and stops after an overrun. This limitation is described in the README.

## Completion matrix

| Requirement | Implementation and evidence | Verdict |
| --- | --- | --- |
| uv project, src layout, console script, optional NLP extra | `pyproject.toml:1`; locked dependencies in `uv.lock`; wheel and sdist under `dist/` | PASS |
| SERP and async AI Overview, PAA, exclusions | `src/content_brief/api.py:48`; `tests/test_parsing.py:19` | PASS |
| Page main text and headings, local readability fallback | `src/content_brief/parsing.py:200`; `src/content_brief/pipeline.py:151`; `tests/test_pipeline.py` fallback integration test | PASS |
| Source-tagged query dedupe, Labs volumes, forced LLM fan-out | `src/content_brief/parsing.py`; `src/content_brief/api.py`; documented fixture integration at `tests/test_pipeline.py:44` | PASS |
| Term counts, densities, coverage threshold, scaled IQR, length band | `src/content_brief/analysis.py:166`; arithmetic tests in `tests/test_analysis.py` | PASS |
| NLP NER/noun chunks and absent-package/model fallback | `src/content_brief/analysis.py:89`; `tests/test_nlp_optional.py:15`; absence tests in `tests/test_analysis.py` | PASS |
| Entity normalization, document frequency, total mentions | `src/content_brief/analysis.py:199`; normalization tests in `tests/test_analysis.py` | PASS |
| Heading clusters and query-to-page/cluster mapping; gaps | `src/content_brief/analysis.py:239`; heading and gap test in `tests/test_analysis.py` | PASS |
| All four outputs and request-hash cache replay | `src/content_brief/output.py:153`; `.cache/pytest-tmp/test_documented_fixture_end_to0/output/` | PASS |
| Offline draft score, grade, and misses | `src/content_brief/analysis.py:357`; score tests in `tests/test_analysis.py`; CLI integration in `tests/test_pipeline.py` | PASS |
| Dry-run without credentials, sockets, or output writes | `tests/test_pipeline.py:127`; `.cache/dry-run.json` | PASS |
| Cost ceiling, task-cost settlement, strict API status checks | `src/content_brief/api.py`; failure and accounting tests in `tests/test_api.py` | PASS with documented variable-price limit |
| No secret values in cache/errors; no auth on fallback | redaction tests in `tests/test_api.py`; fallback HTTP test in `tests/test_parsing.py` | PASS |
| Endpoint docs read; each fixture cites its doc | `tests/fixtures/*.json`; `tests/test_contract.py:19` | PASS |
| Offline tests with real sockets denied | `tests/conftest.py:13`; `tests/test_contract.py:23`; `.cache/verification.log` | PASS |
| MIT, install/credentials/cost/usage README, Claude Code skill | `LICENSE:1`; `README.md:5`; `skill/SKILL.md:1`; skill frontmatter validator passed | PASS |
| Independent external review | No review receipt. Not attempted under this session's network/write restrictions. | UNVERIFIED |
| Authorized live smoke | Deliberately excluded from this build. Requires a separate authorized run. | UNVERIFIED |

## Two local review perspectives

The implementation pass checked documented request/response shapes, CLI behavior, numeric calculations, cache replay, package contents, and optional NLP behavior. It corrected literal plus/percent escaping in SERP keywords and kept successful over-budget responses cached to prevent repeat charges.

The failure-path pass checked request/task failures, missing task costs, malformed responses, transport failures, preflight refusal, failed page parsing, redirect destination validation, auth isolation, and credential redaction. No automatic paid retry path exists. Neither local pass substitutes for independent review.

## Action paths

| Action | Execution path | Gate evidence |
| --- | --- | --- |
| Plan | console script/module -> `cli.main` -> `call_plan` | No HTTP or credentials; offline dry-run test |
| Paid API POST | CLI -> `build_brief` -> `Client.call` | Whole-plan ceiling, per-call estimate, environment credentials, task-cost settlement |
| Direct library POST | `Client.call` | Per-call ceiling and environment credentials; same strict status handling |
| Local fallback GET | `build_brief` -> `fetch_readable` | Only after parse failure; separate unauthenticated client, public destination checks, redirect and size limits |
| Cached rerun | `Client.call` -> request-hash cache | No POST and no credential lookup on cache hit |
| Draft score | CLI -> `score_draft` | Local files only; recorded analysis backend required |

No scheduled process, activation, publisher, sender, remote Git action, or live API retry was introduced.

## Promotion

promotion_verdict:: blocked

Local acceptance checks pass. Independent review and an explicitly authorized live smoke remain outstanding. The fixture suite proves parsing and local behavior; it does not prove live endpoint availability or billing rates.
