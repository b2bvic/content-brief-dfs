# content-brief-dfs

Surfer- and Clearscope-style content briefs, computed from the DataForSEO API.

Give it a keyword. It reads the pages that rank on Google and writes a brief a writer can use:

- **Term frequency and density.** Which terms the ranking pages use, on how many pages, and how often, scaled to your target length.
- **Entities.** The named things (brands, products, organizations, places) that two or more ranking pages mention.
- **Fan-out queries.** People Also Ask, related searches, related keywords with search volume, and the searches ChatGPT runs when you ask it about the keyword.
- **Topic coverage.** Heading clusters across the ranking pages, and which queries no page answers (gaps).
- **A draft score.** Grade a draft against the brief, with a list of what it misses.

The engine counts. It does not write articles. Most open-source "content brief" skills ask a model to judge the terms and the outline; this one measures the pages first, and the optional Claude Code skill writes the outline on top of the measured tables.

A brief costs about **$0.03**, or **$0.05** with ChatGPT fan-out. Reruns from cache are free.

## Example

`content-brief brief "how to clean a cast iron skillet" --llm-fanout` (Google US, November 2026), excerpts from `brief.md`:

```text
Target: 1000 words. Competitor median: 1000; p25-p75: 562.5-1360 words.
Sample: 7 usable pages.

| Term (lemma) | Pages | Competitor range (scaled to target) |
| pan          | 7/7   | 11-26 |
| cast iron    | 7/7   | 15-21 |
| oil          | 7/7   | 4-14  |
| seasoning    | 5/7   | 5-9   |
| paper towel  | 5/7   | 2-4   |
| thin layer   | 5/7   | 1-3   |

| Query                                                          | Sources                  | Volume | Coverage  |
| How to clean a rusty cast iron skillet                         | related_keyword, related | 8100   | 3 page(s) |
| How to clean a cast iron skillet with stains?                  | paa                      | -      | GAP       |
| how to clean a cast iron skillet without soap                  | related_keyword          | 20     | GAP       |
| how to clean cast iron skillet after cooking step by step dry  | chatgpt:howto            | -      | 5 page(s) |
|   oil seasoning Lodge official                                 |                          |        |           |
| cast iron skillet cleaning restoration service cost 2026       | chatgpt:cost             | -      | GAP       |
```

A `GAP` is a question that no ranking page covers. That is where a new page can add something the others do not have.

## Install

You need Python 3.11 to 3.14, [uv](https://docs.astral.sh/uv/), and a [DataForSEO](https://dataforseo.com/) account.

```sh
git clone https://github.com/b2bvic/content-brief-dfs.git
cd content-brief-dfs
uv sync
uv run content-brief --help
```

For better entity detection, install the optional spaCy extra and its English model. Without it, the engine uses a capitalized-phrase heuristic, which is noisier.

```sh
uv sync --extra nlp
uv pip install 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl#sha256=1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85'
```

To install the command globally:

```sh
uv tool install .
```

## Credentials

Set your DataForSEO API login and API password in the environment:

```sh
export DATAFORSEO_LOGIN='you@example.com'
export DATAFORSEO_PASSWORD='your-api-password'
```

The tool does not read `.env` files or credential files. Credentials never enter the cache, the outputs, or error messages. A dry run, a draft score, and a fully cached rerun need no credentials.

## Usage

Preview the calls and the estimated cost. This makes no request and writes nothing:

```sh
uv run content-brief brief 'how to clean a cast iron skillet' --llm-fanout --dry-run
```

Run it:

```sh
uv run content-brief brief 'how to clean a cast iron skillet' --llm-fanout \
  --max-cost 0.10 --output out
```

Score a draft against the brief:

```sh
uv run content-brief score draft.md --brief out/brief.json
uv run content-brief score draft.md --brief out/brief.json --json
```

### Options

| Option | Default | What it does |
| --- | --- | --- |
| `--llm-fanout` | off | Asks the ChatGPT app from five intent angles and records the searches it runs. Adds about $0.02. |
| `--max-cost` | none | Refuses a run whose estimate is above this ceiling, in USD. |
| `--top-n` | 10 | Number of ranking pages to analyze. |
| `--location-code` | 2840 (United States) | DataForSEO location code for the Google market. |
| `--paa-depth` | 0 | Expands People Also Ask by clicking questions, 1 to 4 levels. |
| `--related-limit` | 100 | Number of related keywords to request. |
| `--min-page-fraction` | 0.30 | Share of pages a term must appear on to enter the brief. |
| `--target-words` | competitor median | Length that term ranges scale to. |
| `--exclude-domain` | | Adds a domain to skip. Repeatable. |
| `--include-excluded` | off | Keeps YouTube, Reddit, Quora, Pinterest, Amazon, and Wikipedia in the sample. |
| `--no-nlp` | off | Uses the heuristic entity finder even if spaCy is installed. |
| `--cache-dir` | `.cache/content-brief` | Where raw API responses are cached. |
| `--output` | `out` | Where the four output files go. |

### Outputs

| File | Contents |
| --- | --- |
| `brief.md` | The writer brief: length band, top 60 terms, entities, topic clusters, questions with coverage, AI Overview, and collection notes. |
| `brief.json` | Everything: settings, page text, every metric, query mappings, warnings, and settled costs. |
| `terms.csv` | Every term with per-page counts, densities, and the scaled range. |
| `entities.csv` | Every entity with type, page count, and mentions. |

## How it works

1. **Search results.** One Google Organic call (`serp/google/organic/live/advanced`) with the AI Overview, at depth N + 10 so that excluded domains still leave N pages. DataForSEO currently returns result URLs as `google.com/goto` links; the engine follows Google's redirects to the real page and caches each resolution.
2. **Page content.** One Content Parsing call (`on_page/content_parsing/live`) per page for main text and H2/H3 headings. If parsing returns no text, a local readability extractor fetches the page instead.
3. **Queries.** People Also Ask, related searches, and related keywords with volume (`dataforseo_labs/google/related_keywords/live`). With `--llm-fanout`, five ChatGPT app calls (`ai_optimization/chat_gpt/llm_scraper/live/advanced`). Each query keeps its sources.
4. **Terms.** Lemmatized 1- to 3-word terms, with stop words, generic verbs, and filler removed. A shorter term is dropped when it only ever appears inside a longer one. The range is the middle half (p25 to p75) of competitor usage, counted only on pages that use the term, scaled to the target length.
5. **Entities.** spaCy named entities without dates, money, and counts, or capitalized phrases without spaCy. An entity must appear on two or more pages. A word that the pages mostly write in lower case ("Cook", "Bake") counts as vocabulary, not a name.
6. **Topics.** H2/H3 headings cluster by word overlap, with the keyword's own words and listicle numbering removed. The brief shows clusters that appear on two or more pages.
7. **Coverage.** Each query maps to the pages and clusters that cover its distinct words. A short question must match within one passage; a long fan-out search string must match across the page. A query with no match is a gap.

### Why ChatGPT fan-out uses the app

The searches an answer engine runs show what it checks before it answers. DataForSEO exposes ChatGPT's searches as `fan_out_queries`. In tests on three keywords (November 2026):

- The LLM Responses API (`llm_responses/live`) with forced web search ran one search: the prompt itself. Cost: $0.03 per call.
- The logged-out ChatGPT app (`llm_scraper`) with the bare keyword also ran about one search.
- The same app asked from five angles (bare keyword, a buyer, a comparison, a how-to, cost) ran 10 to 11 distinct searches per keyword. Cost: $0.004 per call.

So `--llm-fanout` uses the app with five angles, and tags each query `chatgpt:<angle>`.

## Cost

Each request carries one task. Settled costs come from each task's `cost` field and are recorded in `brief.json`.

| Endpoint | Price | Default run |
| --- | --- | --- |
| `serp/google/organic/live/advanced` | $0.002 per 10 results; +$0.002 for the async AI Overview | $0.006 (depth 20) |
| `on_page/content_parsing/live` | $0.00015 per page | up to $0.0015 |
| `dataforseo_labs/google/related_keywords/live` | $0.012 per task + $0.00012 per item | $0.024 |
| `ai_optimization/chat_gpt/llm_scraper/live/advanced` | $0.004 per task | $0.020 with `--llm-fanout` |

Pricing: [SERP](https://dataforseo.com/pricing/google-serp/google-organic-serp-api), [OnPage](https://dataforseo.com/pricing/on-page/onpage-api), [Labs](https://dataforseo.com/pricing/dataforseo-labs/dataforseo-google-api), [AI Optimization](https://dataforseo.com/pricing/ai-optimization/llm-scraper). Prices checked 2026.11.10.

`--max-cost` refuses a plan above the ceiling before any request. It checks again before each uncached call and stops after an unexpected overrun. No paid call is retried automatically. A missing cost, an HTTP failure, or invalid JSON stops the run, because the spend is then unknown.

Raw responses are cached by a hash of the endpoint and the request. An identical rerun costs nothing and needs no credentials. Changes that only affect analysis (`--min-page-fraction`, `--target-words`, `--no-nlp`) reuse the cache. To refresh data, use a new `--cache-dir`. The cache holds third-party page text; keep it private.

## Scoring

`score` grades a draft from 0 to 100:

| Component | Weight | Credit |
| --- | --- | --- |
| Terms (the 60 in `brief.md`) | 50% | Full inside the range; `uses/min` below it; `max/uses` above it. |
| Entities | 20% | Mentioned or not. |
| Topics (clusters on two or more pages) | 20% | Covered or not. |
| Word count | 10% | Full inside the p25 to p75 band; proportional outside it. |

The score matches words, not meaning. It does not count "price" as "cost". It is an editing aid, not a ranking prediction. A range describes what competitors do; it is not a quota.

## Claude Code skill

`skill/SKILL.md` is a [Claude Code](https://claude.com/claude-code) skill. Copy it to `.claude/skills/content-brief/SKILL.md` in a project where the CLI is installed, then ask for a brief. The skill runs the CLI, reads `brief.json`, and adds a search-intent note, an angle, an H2/H3 outline, and section-by-section term and entity guidance. It never invents a count, a volume, or a cost that the engine did not compute, and it does not write the article unless you ask.

## Limits

- English only. `--location-code` selects the Google market.
- Small samples give unstable ranges. The brief shows the sample size.
- spaCy's small English model misses some names and mislabels others. Skim the entity table.
- Topic coverage is lexical. A match means the words are there, not that the question is answered well.
- ChatGPT fan-out comes from the logged-out app. Logged-in answers can differ.

## Tests

```sh
uv run pytest
```

Every test blocks real network access and DNS. HTTP calls use `httpx.MockTransport` with fixtures built from DataForSEO's documented and live responses. Each fixture names its source.

## License

MIT. See [LICENSE](LICENSE).
