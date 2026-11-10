import httpx
import pytest

from content_brief.api import APIError
from content_brief.parsing import (
    excluded,
    llm_queries,
    merge_queries,
    organic_pages,
    parsed_page,
    query_row,
    readable_page,
    related_queries,
    serp_features,
)
from content_brief.pipeline import check_public_destination, fetch_readable


def test_documented_serp_shapes(fixture_response):
    raw = fixture_response("serp")
    pages, _ = organic_pages(raw, 10, ())
    assert pages[0]["url"] == "https://www.t-mobile.com/cell-phone/apple-iphone-12-pro"
    rows, overviews = serp_features(raw)
    assert any(
        q["query"] == "iphone xr" and q["sources"] == ["related_search"] for q in rows
    )
    assert any(q["query"] == "What are the symptoms of HIDS disease?" for q in rows)
    assert "BMW M4 F82" in overviews[0]["text"]
    assert any("autoevolution.com" in r["url"] for r in overviews[0]["references"])


def test_documented_content_parsing_excludes_navigation(fixture_response):
    page = parsed_page(fixture_response("pages"), "https://example.org/", "Example")
    assert "Search trends data is a treasure trove" in page.text
    assert (
        "https://api.dataforseo.com/v3/keywords_data" in page.text
    )  # This URL is documented article text.
    assert any(h["level"] == 3 and "Trends API" in h["text"] for h in page.headings)
    assert not any(h["text"] == "No Comments" for h in page.headings)
    assert "Terms of Services" not in page.text


def test_documented_related_keyword_volume(fixture_response):
    rows = related_queries(fixture_response("keywords"))
    assert rows[0] == {
        "query": "phone",
        "sources": ["related_keyword"],
        "search_volume": 368000,
    }


def test_chatgpt_app_fanout_is_result_level_and_tagged_by_angle(fixture_response):
    rows = llm_queries(fixture_response("llm"), "compare")
    assert len(rows) == 3
    assert all(row["sources"] == ["chatgpt:compare"] for row in rows)
    assert rows[2]["query"].startswith("technical SEO agency vs consultant")


def test_deduplicate_preserves_sources_and_volume():
    rows = merge_queries(
        [
            query_row("Phone?", "paa"),
            query_row(" phone ", "related_keyword", 100),
            query_row("PHONE", "llm_fanout"),
        ]
    )
    assert len(rows) == 1
    assert rows[0]["search_volume"] == 100
    assert set(rows[0]["sources"]) == {"paa", "related_keyword", "llm_fanout"}


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.reddit.com/r/x", True),
        ("https://old.reddit.com/", True),
        ("https://notreddit.com/", False),
        ("https://reddit.com.example.org/", False),
    ],
)
def test_domain_exclusions_use_host_boundary(url, expected):
    assert excluded(url, ("reddit.com",)) is expected


def test_organic_selection_dedupes_filters_and_ranks(fixture_response):
    raw = fixture_response("serp")
    raw["tasks"][0]["result"][0]["items"] = [
        {"type": "organic", "url": "https://reddit.com/x", "rank_group": 1},
        {"type": "organic", "url": "https://example.org/a#one", "rank_group": 3},
        {"type": "organic", "url": "https://example.org/b", "rank_group": 2},
        {"type": "organic", "url": "https://example.org/a#two", "rank_group": 4},
        {"type": "organic", "url": "file:///tmp/test", "rank_group": 5},
    ]
    pages, skipped = organic_pages(raw, 2, ("reddit.com",))
    assert [p["url"] for p in pages] == [
        "https://example.org/b",
        "https://example.org/a",
    ]
    assert len(skipped) == 2


def test_readability_prefers_article_and_strips_boilerplate():
    page = readable_page(
        """<html><nav>Menu Privacy Cookies</nav><main><h2>Battery life</h2>
      <p>Apple phones have a useful battery life.</p><p>Choose a phone with long battery life.</p>
      <aside>Subscribe Newsletter</aside><script>fake text</script></main><footer>All rights reserved</footer></html>""",
        "https://example.org",
    )
    assert page.source == "local_readability"
    assert page.headings == [{"level": 2, "text": "Battery life"}]
    assert "Apple phones" in page.text
    assert "Newsletter" not in page.text
    assert "Privacy" not in page.text


def test_fallback_density_handles_non_semantic_html():
    page = readable_page(
        '<div><p>Long useful prose on batteries.</p></div><div><a href="/">Menu link</a></div>',
        "https://example.org",
    )
    assert page.text == "Long useful prose on batteries."


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_fallback_refuses_private_destinations(monkeypatch, ip):
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 80))]
    )
    with pytest.raises(APIError, match="non-public"):
        check_public_destination("http://example.org/")


def test_fallback_http_follows_validated_redirect_without_auth(monkeypatch):
    from content_brief import pipeline

    destinations, requests = [], []
    monkeypatch.setattr(
        pipeline, "check_public_destination", lambda url: destinations.append(url)
    )

    def handler(request):
        requests.append(request)
        assert "authorization" not in request.headers
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "/article"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<article><h2>Guide</h2><p>Useful article prose.</p></article>",
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        page = fetch_readable("https://example.org/", "Guide", http)
    assert len(requests) == 2
    assert destinations == ["https://example.org/", "https://example.org/article"]
    assert page.text == "Useful article prose."
