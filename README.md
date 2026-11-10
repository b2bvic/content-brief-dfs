# content-brief-dfs

Compute a writer brief from Google competitors using DataForSEO. The engine counts terms, entity mentions, headings, and query coverage. It does not write articles or call an article-writing model.

## Install

Use Python 3.11 through 3.14 and [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run content-brief --help
uv run pytest
```

The package uses a `src/content_brief` layout. To install its console script into an isolated tool environment:

```sh
uv tool install .
content-brief brief 'test keyword' --dry-run
```

The base install does not require spaCy. To enable its lemmatizer and named entities (recommended; the fallback is noisier):

```sh
uv sync --extra nlp
uv pip install 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl#sha256=1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85'
```

The model is a separate download. If either spaCy or the model is absent, the engine uses English heuristics. `--no-nlp` forces that fallback. Scoring uses the brief's recorded backend and refuses a missing NLP model instead of silently changing metrics. This version analyzes English text; `--location-code` selects the search market.

## Credentials

Set `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` in the process environment through your secret manager or shell. They are your DataForSEO API login and API password. The client uses HTTP Basic authentication.

The application does not read dotenv files, shell profiles, or credential files. Never commit credentials. Auth headers never enter cache keys or output. Credential values echoed in API response strings are redacted before caching. Errors omit API response bodies and transport exception details.

Dry runs, draft scoring, and fully cached reruns require no credentials. The optional local page fallback uses a separate HTTP client without DataForSEO authentication.

## Cost

Prices below were checked on 2026-11-10. Each POST contains one task. Current pricing pages take precedence over historical costs shown in doc examples.

| DataForSEO endpoint | Documented USD charge | This tool's default estimate |
| --- | --- | --- |
| [`serp/google/organic/live/advanced`](https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/) | $0.002 per SERP of up to 10 results; +$0.002 for async AI Overview when present | $0.004 for depth 10 with async AI Overview |
| [`on_page/content_parsing/live`](https://docs.dataforseo.com/v3/on_page/content_parsing/live/) | $0.00015 per page | Up to 10 calls, $0.00150 total |
| [`dataforseo_labs/google/related_keywords/live`](https://docs.dataforseo.com/v3/dataforseo_labs/google/related_keywords/live/) | $0.012 per task + $0.00012 per returned item | $0.024 at the default limit of 100 |
| [`ai_optimization/chat_gpt/llm_scraper/live/advanced`](https://docs.dataforseo.com/v3/ai_optimization/chat_gpt/llm_scraper/live/advanced/) | $0.004 per task (settled in live runs, 2026.11.10) | Optional; five tasks, $0.020 |

Pricing sources: [Google Organic SERP](https://dataforseo.com/pricing/google-serp/google-organic-serp-api), [OnPage](https://dataforseo.com/pricing/on-page/onpage-api), [Google Labs](https://dataforseo.com/pricing/dataforseo-labs/dataforseo-google-api), [LLM Responses](https://dataforseo.com/pricing/ai-optimization/llm-responses).

The default cold-cache plan estimates **$0.03150**. Adding `--llm-fanout` estimates **$0.05150**. These are estimates, not fixed quotes; settled task costs are recorded in `brief.json`.

`--llm-fanout` asks the logged-out ChatGPT app the keyword from five intent angles (bare, buyer, compare, how-to, cost) with web search forced, and records the searches ChatGPT ran (`fan_out_queries`). Each query is tagged `chatgpt:<angle>`. A bare keyword usually makes ChatGPT run one search; the angled prompts are what make it fan out. In live tests on three keywords, the LLM Responses API route returned only the prompt itself as its search, so this tool does not use it.

SERP depth rounds up to groups of ten. Search operators increase the base rate. PAA expansion costs $0.00015 per click. The plan reserves `4*(2**depth-1)` clicks, since the server determines the real number. It estimates fivefold base-price multiplication per detected search operator. The docs describe these variable charges; actual settlement always uses the task `cost` field.

`--max-cost` refuses a plan above the ceiling before sending requests. It checks the remaining allowance before each uncached call and stops after an unexpected settled overrun. It cannot guarantee a hard cap on an in-flight, variably priced request. Failed tasks with reported costs are also settled. Missing costs, HTTP failures, and invalid JSON stop the run because spending is unknown. No paid call is retried automatically.

Successful raw API responses are cached by SHA-256 of endpoint and canonical request JSON under `.cache/content-brief/`. Credentials are excluded from that hash. Identical reruns cost zero and can run without credentials. Parameters affecting only local analysis reuse API data. Changed API parameters create new entries. To refresh stale data, use a new `--cache-dir` or remove selected cache files yourself. Cache data contains third-party page text; keep it private where needed.

## Usage

Preview a call plan without credentials, network access, or file writes:

```sh
uv run content-brief brief 'test keyword' --dry-run
uv run content-brief brief 'solar battery storage' --llm-fanout --dry-run
```

Collect data and write the four artifacts:

```sh
uv run content-brief brief 'solar battery storage' --max-cost 0.05 --output out
uv run content-brief brief 'solar battery storage' --llm-fanout \
  --max-cost 0.10 --output out
```

Other controls:

```sh
uv run content-brief brief 'solar battery storage' --top-n 20 --paa-depth 1 \
  --related-limit 200 --min-page-fraction 0.4 --target-words 1800 \
  --exclude-domain example.org --location-code 2840 --dry-run
uv run content-brief brief 'solar battery storage' --include-excluded --no-nlp --dry-run
```

By default, the tool excludes `youtube.com`, `youtu.be`, `reddit.com`, `quora.com`, `pinterest.com`, `amazon.com`, and `wikipedia.org`, including subdomains. Use `--include-excluded` to include them. Repeat `--exclude-domain` for other domains, including regional variants. Exclusions operate on the returned SERP; the tool does not buy extra SERPs to replace excluded results. The sample may therefore contain fewer than N pages. Skips and parsing failures appear in the brief.

Content Parsing supplies main text and H2/H3 headings. If parsing fails, a local readability extractor selects an article/main container or the block with the most non-link prose. It removes navigation, forms, scripts, and sidebars. Local fetches check public network destinations, validate redirects, limit HTML to 5 MB, and cache successful extractions.

Outputs:

| File | Contents |
| --- | --- |
| `brief.md` | Writer-facing length band, tables, topics, questions, AI Overview, and collection notes |
| `brief.json` | Schema version 1: settings, source pages and text, all metrics, query mappings, warnings, and cost records |
| `terms.csv` | Term document frequency, per-page counts and densities, and target-length usage ranges |
| `entities.csv` | Normalized entity, type, page frequency, and mention counts |

Per-page arrays use the same order as `pages` in `brief.json`. Existing output files in `--output` are replaced. CSV formula prefixes are escaped for spreadsheet safety.

Score a draft offline:

```sh
uv run content-brief score draft.md --brief out/brief.json
uv run content-brief score draft.md --brief out/brief.json --json
```

The report includes term counts and ranges, missing entities, missing topics, word count, and a 0-100 grade. The score is an editing aid, not a ranking prediction.

## Computation

- Word count uses lexical words and numbers in main text. The default target is the rounded median competitor length, with a p25-p75 band.
- Terms are lemmatized 1-3 grams. Stop words and boilerplate break grams. Hyphens separate tokens within compounds; other punctuation and sentence boundaries break grams. A term needs at least 30% page coverage by default.
- Each term's count is divided by that page's word count, then scaled to target length. Linear inclusive p25 and p75 use only pages containing the term. Recommendations round outward and require at least one use.
- spaCy supplies named entities; numeric labels (dates, money, counts) are dropped. The fallback finds capitalized phrases, skips a single capitalized word at a sentence start, and normalizes case and common English plurals. Its candidates can contain false positives. An entity must appear on at least two pages (when the sample has three or more), and a word the pages mostly write in lower case is treated as vocabulary, not a name.
- The keyword's own words are removed before heading clustering and query matching, so a query is a gap only when no page covers its distinct words.
- Organic URLs that DataForSEO returns as `google.com/goto` links are resolved by following Google's redirects (including meta-refresh interstitials) before exclusion and parsing. Resolutions are cached.
- H2/H3 clusters use greedy token Jaccard overlap of at least 0.4. Query mappings use at least 60% query-token overlap with a heading or an 80-word text window. Matches indicate lexical coverage, not verified answers. Unmatched queries are gaps.
- Fan-out merges PAA, related searches, AI Overview subheadings, Labs keywords with volume, and optional result-level `fan_out_queries`. When an Overview element has no title, its first source sentence is the subtopic. No model invents queries from that text.
- Scoring weights are terms 50%, entities 20%, topics 20%, length 10%. Empty metric groups are omitted and the remaining weights renormalize. In-range terms earn full credit. Underuse earns `actual/min`; overuse earns `max/actual`. Entities and topics are binary. Length earns full credit inside the length band and proportional credit outside it. An explicit target scales that band proportionally.

The heuristic lemmatizer is intentionally approximate. Language support, semantic judgment, and fact checking remain editorial responsibilities. Small samples produce unstable ranges; the output exposes sample size.

## Claude Code skill

Copy `skill/SKILL.md` into `.claude/skills/content-brief/SKILL.md` in the project where the CLI is installed. Invoke `/content-brief` with a keyword or an existing output directory. The skill reads computed data and adds the outline, intent note, angle, and section guidance. It must not invent usage counts, volumes, frequencies, or costs.

## Offline validation

```sh
UV_OFFLINE=1 uv run pytest
uv run content-brief brief 'test keyword' --dry-run
```

Install dependencies once before an offline run. Every pytest test blocks real socket connections and DNS resolution. HTTP behavior uses `httpx.MockTransport`. Each JSON fixture cites its endpoint doc and identifies its reductions. Long example prose is removed or replaced with labeled synthetic text. The examples cover different topics; they test response shapes and pipeline output, not a coherent live competitor sample. Synthetic unit-test pages separately verify the calculations.

The fixture end-to-end test writes `brief.md`, `brief.json`, `terms.csv`, and `entities.csv`, then replays the cache with no credentials and a zero ceiling. Test artifacts stay in `.cache/pytest-tmp`. No live API smoke run is part of these tests.

MIT licensed. Publication and live API use are separate operator actions.
