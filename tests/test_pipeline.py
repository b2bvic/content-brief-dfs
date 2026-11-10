import csv
import json
from decimal import Decimal

import httpx
import pytest

from content_brief.analysis import Analyzer, score_draft
from content_brief.api import (
    KEYWORDS,
    LLM,
    PAGES,
    SERP,
    Cache,
    Client,
    CostError,
    UnknownCostError,
)
from content_brief.cli import main
from content_brief.config import BriefError, Options
from content_brief.output import write_brief
from content_brief.pipeline import build_brief, call_plan


def fixture_client(tmp_path, fixture_response, extra_handler=None, max_cost=None):
    calls = []
    mapping = {SERP: "serp", PAGES: "pages", KEYWORDS: "keywords", LLM: "llm"}

    def handler(request):
        calls.append(request)
        if extra_handler:
            response = extra_handler(request)
            if response is not None:
                return response
        return httpx.Response(200, json=fixture_response(mapping[request.url.path]))

    return Client(
        Cache(tmp_path / "cache"),
        max_cost,
        httpx.Client(transport=httpx.MockTransport(handler)),
    ), calls


def test_documented_fixture_end_to_end_writes_four_outputs(
    tmp_path, fixture_response, credentials, monkeypatch
):
    client, calls = fixture_client(tmp_path, fixture_response)
    options = Options("test keyword", llm_fanout=True)
    brief = build_brief(options, client, Analyzer(False))
    paths = write_brief(brief, tmp_path / "output")
    assert {path.name for path in paths} == {
        "brief.md",
        "brief.json",
        "terms.csv",
        "entities.csv",
    }
    assert all(path.stat().st_size > 0 for path in paths)
    loaded = json.loads((tmp_path / "output/brief.json").read_text())
    assert loaded["schema_version"] == 1
    assert loaded["terms"] and loaded["entities"] and loaded["topics"]
    assert loaded["pages"][0]["word_count"] > 100
    # SERP, one page, related keywords, and five ChatGPT intent angles.
    assert len(calls) == 8
    prompts = [json.loads(c.content)[0] for c in calls[3:]]
    assert all(p["force_web_search"] is True for p in prompts)
    assert prompts[0]["keyword"] == "test keyword"
    assert len({p["keyword"] for p in prompts}) == 5
    # 0.013425 for SERP, page, and keywords, plus 5 x 0.004.
    assert loaded["cost"]["settled_usd"] == "0.033425"
    assert any(s.startswith("chatgpt:") for q in loaded["queries"] for s in q["sources"])
    with (tmp_path / "output/terms.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(loaded["terms"])
    assert "Writer brief" in (tmp_path / "output/brief.md").read_text()
    assert 0 <= score_draft(loaded["pages"][0]["text"], loaded)["grade"] <= 100
    # Warm rerun with no credentials and a zero ceiling uses only request hashes.
    monkeypatch.delenv("DATAFORSEO_LOGIN")
    monkeypatch.delenv("DATAFORSEO_PASSWORD")
    replay = Client(client.cache, Decimal(0))
    try:
        cached = build_brief(
            Options("test keyword", llm_fanout=True, max_cost=Decimal(0)),
            replay,
            Analyzer(False),
        )
    finally:
        replay.close()
    assert cached["cost"]["settled_usd"] == "0"
    assert all(record["cached"] for record in cached["cost"]["calls"])
    assert cached["terms"] == brief["terms"]


def test_default_omits_llm(tmp_path, fixture_response, credentials):
    client, calls = fixture_client(tmp_path, fixture_response)
    build_brief(Options("test keyword"), client, Analyzer(False))
    assert LLM not in [request.url.path for request in calls]


def test_fixture_cli_brief_and_score(
    tmp_path, fixture_response, credentials, monkeypatch, capsys
):
    from content_brief import cli

    client, _ = fixture_client(tmp_path, fixture_response)
    monkeypatch.setattr(cli, "Client", lambda *args: client)
    output = tmp_path / "out"
    assert (
        main(
            [
                "brief",
                "test keyword",
                "--no-nlp",
                "--output",
                str(output),
                "--cache-dir",
                str(client.cache.root),
            ]
        )
        == 0
    )
    capsys.readouterr()
    draft = tmp_path / "draft.md"
    draft.write_text("# Test draft\n\nGoogle Trends measures search interest.")
    assert (
        main(["score", str(draft), "--brief", str(output / "brief.json"), "--json"])
        == 0
    )
    assert 0 <= json.loads(capsys.readouterr().out)["grade"] <= 100


def test_dry_run_no_creds_network_or_writes(tmp_path, capsys):
    cache = tmp_path / "absent-cache"
    output = tmp_path / "absent-output"
    assert (
        main(
            [
                "brief",
                "test keyword",
                "--dry-run",
                "--cache-dir",
                str(cache),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    assert plan["estimated_cost_usd"] == "0.03150"
    assert {row["endpoint"] for row in plan["calls"]} == {SERP, PAGES, KEYWORDS}
    assert not cache.exists() and not output.exists()


def test_max_cost_preflight_refuses_entire_run(tmp_path, fixture_response):
    client, calls = fixture_client(
        tmp_path, fixture_response, max_cost=Decimal("0.001")
    )
    with pytest.raises(CostError):
        build_brief(
            Options("phone", max_cost=Decimal("0.001")), client, Analyzer(False)
        )
    assert not calls


def test_click_depth_operator_and_result_limit_affect_estimates(tmp_path):
    base = call_plan(Options("phone"), Cache(tmp_path))
    expanded = call_plan(
        Options(
            "site:example.org phone",
            top_n=20,
            paa_depth=2,
            related_limit=200,
            llm_fanout=True,
        ),
        Cache(tmp_path),
    )
    assert Decimal(expanded["estimated_cost_usd"]) > Decimal(base["estimated_cost_usd"])
    assert expanded["calls"][0]["estimated_per_call_usd"] == "0.03380"
    assert expanded["calls"][-1]["endpoint"] == LLM


def test_parse_failure_uses_local_fallback_and_caches_it(
    tmp_path, fixture_response, credentials, monkeypatch
):
    from content_brief import pipeline

    monkeypatch.setattr(pipeline, "check_public_destination", lambda url: None)

    def failed_page(request):
        if request.url.path == PAGES:
            raw = fixture_response("pages")
            raw["tasks"][0]["status_code"] = 40602
            return httpx.Response(200, json=raw)

    client, _ = fixture_client(tmp_path, fixture_response, failed_page)

    def fallback(request):
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<article><h2>Phone batteries</h2><p>Apple makes phones. Battery life is useful.</p></article>",
        )

    with httpx.Client(transport=httpx.MockTransport(fallback)) as http:
        brief = build_brief(Options("phone"), client, Analyzer(False), http)
    assert brief["pages"][0]["source"] == "local_readability"
    assert brief["warnings"]
    assert client.spent == Decimal("0.013425")
    replay = Client(client.cache, Decimal(0))
    try:
        rerun = build_brief(
            Options("phone", max_cost=Decimal(0)), replay, Analyzer(False)
        )
    finally:
        replay.close()
    assert rerun["cost"]["settled_usd"] == "0"
    assert rerun["pages"][0]["source"] == "local_readability"


def test_unknown_cost_never_triggers_fallback_or_more_calls(
    tmp_path, fixture_response, credentials
):
    def unknown(request):
        if request.url.path == PAGES:
            raw = fixture_response("pages")
            raw["tasks"][0].pop("cost")
            return httpx.Response(200, json=raw)

    client, calls = fixture_client(tmp_path, fixture_response, unknown)
    with pytest.raises(UnknownCostError):
        build_brief(Options("phone"), client, Analyzer(False))
    assert len(calls) == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"top_n": 0},
        {"top_n": 101},
        {"paa_depth": 5},
        {"related_limit": 0},
        {"min_page_fraction": 0},
        {"min_page_fraction": float("nan")},
        {"target_words": -1},
        {"max_cost": Decimal("NaN")},
        {"language_code": "fr"},
        {"location_code": 0},
    ],
)
def test_invalid_options_rejected(kwargs):
    with pytest.raises(BriefError):
        Options("phone", **kwargs)


def test_oversize_llm_prompt_rejected():
    with pytest.raises(BriefError):
        Options("x" * 1801, llm_fanout=True)


def test_google_goto_urls_resolve_before_exclusion(tmp_path, monkeypatch):
    from content_brief import pipeline
    from content_brief.api import Cache
    from content_brief.parsing import organic_pages

    monkeypatch.setattr(pipeline, "check_public_destination", lambda url: None)
    hops = {
        "https://google.com/goto?url=A": "https://www.google.com/goto?url=A",
        "https://www.google.com/goto?url=A": "https://www.seriouseats.com/restore-cast-iron",
        "https://google.com/goto?url=B": "https://www.reddit.com/r/castiron/",
    }

    def handler(request):
        return httpx.Response(301, headers={"location": hops[str(request.url)]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    raw = {
        "status_code": 20000,
        "tasks": [{"status_code": 20000, "cost": 0.004, "result": [{"items": [
            {"type": "organic", "rank_group": 1, "domain": "google.com",
             "url": "https://google.com/goto?url=B", "title": "r/castiron"},
            {"type": "organic", "rank_group": 2, "domain": "google.com",
             "url": "https://google.com/goto?url=A", "title": "Restore"},
            {"type": "organic", "rank_group": 3, "domain": "google.com",
             "url": "https://google.com/goto?url=C", "title": "Broken",
             "breadcrumb": "https://www.youtube.com › watch"},
        ]}]}],
    }

    def failing(request):
        if "url=C" in str(request.url):
            return httpx.Response(200, text="<html></html>")
        return handler(request)

    http = httpx.Client(transport=httpx.MockTransport(failing))
    prepared, warnings, pending = pipeline.prepare_serp_urls(
        raw, Cache(tmp_path), ("reddit.com", "youtube.com"), http
    )
    assert not pending
    candidates, skipped = organic_pages(prepared, 10, ("reddit.com", "youtube.com"))
    assert [c["url"] for c in candidates] == ["https://www.seriouseats.com/restore-cast-iron"]
    reasons = sorted(s["reason"] for s in skipped)
    assert reasons == ["excluded_domain", "excluded_domain"]
    assert warnings and warnings[0]["fallback_domain"] == "youtube.com"
    # The resolution is cached; a second pass needs no network.
    again, _, pending = pipeline.prepare_serp_urls(
        raw, Cache(tmp_path), ("reddit.com",), allow_network=False
    )
    assert any(i["url"].startswith("https://www.seriouseats.com") for i in again["tasks"][0]["result"][0]["items"])


def test_google_meta_refresh_interstitial_is_followed(tmp_path, monkeypatch):
    from content_brief import pipeline
    from content_brief.api import Cache

    monkeypatch.setattr(pipeline, "check_public_destination", lambda url: None)

    def handler(request):
        url = str(request.url)
        if url == "https://google.com/goto?url=X":
            return httpx.Response(200, text='<html><head><meta content="5;url=/goto?url=Y&amp;s=1" http-equiv="refresh"></head></html>')
        if url == "https://google.com/goto?url=Y&s=1":
            return httpx.Response(302, headers={"location": "https://www.lodgecastiron.com/pages/how-to-clean"})
        raise AssertionError(url)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert pipeline.resolve_google_url("https://google.com/goto?url=X", Cache(tmp_path), http) == "https://www.lodgecastiron.com/pages/how-to-clean"


def test_throttled_google_hop_is_retried(tmp_path, monkeypatch):
    from content_brief import pipeline
    from content_brief.api import Cache

    monkeypatch.setattr(pipeline, "check_public_destination", lambda url: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, text="<html>unusual traffic</html>")
        return httpx.Response(302, headers={"location": "https://neilpatel.com/technical-seo/"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert pipeline.resolve_google_url("https://google.com/goto?url=Z", Cache(tmp_path), http) == "https://neilpatel.com/technical-seo/"
