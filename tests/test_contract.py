"""Evidence checks for fixture provenance and the offline execution boundary."""

import socket

import pytest

from content_brief.api import DOCS


@pytest.mark.parametrize(
    "name,endpoint",
    [
        ("serp", "/v3/serp/google/organic/live/advanced"),
        ("pages", "/v3/on_page/content_parsing/live"),
        ("keywords", "/v3/dataforseo_labs/google/related_keywords/live"),
        ("llm", "/v3/ai_optimization/chat_gpt/llm_responses/live"),
    ],
)
def test_every_endpoint_fixture_cites_its_doc(fixture_response, name, endpoint):
    assert fixture_response(name)["_fixture"]["source_url"] == DOCS[endpoint]


def test_test_environment_blocks_real_network():
    with socket.socket() as connection:
        with pytest.raises(AssertionError, match="blocked"):
            connection.connect(("127.0.0.1", 80))
    with pytest.raises(AssertionError, match="blocked"):
        socket.getaddrinfo("example.org", 443)
