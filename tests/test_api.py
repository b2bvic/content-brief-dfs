import base64
import json
from decimal import Decimal

import httpx
import pytest

from content_brief.api import (
    SERP,
    APIError,
    Cache,
    Client,
    CostError,
    UnknownCostError,
    serp_payload,
    validate_response,
)
from content_brief.config import Options


def client_for(tmp_path, raw, handler=None, max_cost=None):
    http = httpx.Client(
        transport=httpx.MockTransport(
            handler or (lambda request: httpx.Response(200, json=raw))
        )
    )
    return Client(Cache(tmp_path / "cache"), max_cost, http)


def test_serp_preserves_literal_plus_and_percent():
    assert serp_payload(Options("C++ 50%"))["keyword"] == "C%2B%2B 50%25"


def test_basic_auth_task_body_and_cost_from_task(
    tmp_path, fixture_response, credentials
):
    raw = fixture_response("serp")
    raw["cost"] = 999
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url == "https://api.dataforseo.com" + SERP
        assert request.method == "POST"
        assert json.loads(request.content) == [{"keyword": "phone"}]
        auth = base64.b64decode(request.headers["authorization"].split()[1]).decode()
        assert auth == "fixture-login@example.invalid:fixture-password-only"
        return httpx.Response(200, json=raw)

    client = client_for(tmp_path, raw, handler)
    client.call(SERP, {"keyword": "phone"}, Decimal("0.004"))
    assert client.spent == Decimal("0.003")
    assert len(seen) == 1


@pytest.mark.parametrize("level", ["request", "task"])
@pytest.mark.parametrize(
    "status", [20100, 20101, 40000, 40100, 40602, 50000, None, "20000"]
)
def test_every_non_20000_status_fails(
    tmp_path, fixture_response, credentials, level, status
):
    raw = fixture_response("serp")
    target = raw if level == "request" else raw["tasks"][0]
    target["status_code"] = status
    client = client_for(tmp_path, raw)
    with pytest.raises(APIError, match="status is not 20000"):
        client.call(SERP, {"keyword": "phone"}, Decimal("0.004"))
    assert client.spent == Decimal("0.003")
    assert not list((tmp_path / "cache").glob("*.json"))


@pytest.mark.parametrize("cost", [None, -1, True, "NaN", "Infinity", "oops"])
def test_unknown_task_cost_aborts(tmp_path, fixture_response, credentials, cost):
    raw = fixture_response("serp")
    raw["tasks"][0]["cost"] = cost
    client = client_for(tmp_path, raw)
    with pytest.raises(UnknownCostError):
        client.call(SERP, {}, Decimal("0.004"))


def test_cache_replay_needs_no_credentials_and_costs_zero(
    tmp_path, fixture_response, credentials, monkeypatch
):
    client = client_for(tmp_path, fixture_response("serp"))
    first = client.call(SERP, {"keyword": "phone"}, Decimal("0.004"))
    monkeypatch.delenv("DATAFORSEO_LOGIN")
    monkeypatch.delenv("DATAFORSEO_PASSWORD")
    replay = Client(client.cache, Decimal(0))
    try:
        assert replay.call(SERP, {"keyword": "phone"}, Decimal("0.004")) == first
        assert replay.spent == 0
        assert replay.records[0]["cached"]
        assert replay.records[0]["task_cost_usd"] == "0.003"
        assert client.cache.path(
            SERP, {"keyword": "phone", "depth": 10}
        ) != client.cache.path(SERP, {"keyword": "phone"})
    finally:
        replay.close()


def test_budget_refuses_before_http_or_credentials(tmp_path, fixture_response):
    def forbidden(request):
        pytest.fail("must refuse before HTTP")

    client = client_for(tmp_path, fixture_response("serp"), forbidden, Decimal("0.001"))
    with pytest.raises(CostError):
        client.call(SERP, {}, Decimal("0.004"))
    assert client.spent == 0


def test_actual_settlement_stops_further_spending(
    tmp_path, fixture_response, credentials
):
    client = client_for(tmp_path, fixture_response("serp"), max_cost=Decimal("0.002"))
    with pytest.raises(CostError, match="settled"):
        client.call(SERP, {}, Decimal("0.001"))
    assert client.spent == Decimal("0.003")


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_failure_no_retry_or_secret_error(
    tmp_path, fixture_response, credentials, status
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="fixture-password-only")

    client = client_for(tmp_path, {}, handler)
    with pytest.raises(UnknownCostError) as caught:
        client.call(SERP, {}, Decimal(0))
    assert len(calls) == 1
    assert "fixture-password-only" not in str(caught.value)


def test_credentials_are_redacted_from_body_cache_and_return(
    tmp_path, fixture_response, credentials
):
    raw = fixture_response("serp")
    raw["echo"] = {
        "fixture-password-only": [
            "fixture-login@example.invalid",
            "fixture-password-only",
        ]
    }
    client = client_for(tmp_path, raw)
    response = client.call(SERP, {}, Decimal(0))
    body = json.dumps(response) + next((tmp_path / "cache").glob("*.json")).read_text()
    assert "fixture-password-only" not in body
    assert "fixture-login@example.invalid" not in body
    assert "[REDACTED]" in body


@pytest.mark.parametrize(
    "mutation", ["empty_tasks", "no_tasks", "empty_results", "bad_result", "extra_task"]
)
def test_malformed_success_response_rejected(fixture_response, mutation):
    raw = fixture_response("serp")
    if mutation == "empty_tasks":
        raw["tasks"] = []
    elif mutation == "no_tasks":
        raw.pop("tasks")
    elif mutation == "empty_results":
        raw["tasks"][0]["result"] = []
    elif mutation == "bad_result":
        raw["tasks"][0]["result"] = ["bad"]
    else:
        raw["tasks"].append(raw["tasks"][0])
    with pytest.raises(APIError):
        validate_response(raw)


def test_corrupt_cache_is_not_replayed(tmp_path, fixture_response, credentials):
    client = client_for(tmp_path, fixture_response("serp"))
    client.cache.root.mkdir()
    client.cache.path(SERP, {}).write_text("broken json")
    assert client.call(SERP, {}, Decimal(0))["status_code"] == 20000
    assert client.spent == Decimal("0.003")


def test_successful_overrun_still_caches_to_avoid_repeat_charge(
    tmp_path, fixture_response, credentials
):
    client = client_for(tmp_path, fixture_response("serp"), max_cost=Decimal("0.002"))
    with pytest.raises(CostError):
        client.call(SERP, {}, Decimal("0.001"))
    assert client.cache.read(SERP, {}) is not None
    replay = Client(client.cache, Decimal(0))
    try:
        assert replay.call(SERP, {}, Decimal("0.003"))["status_code"] == 20000
        assert replay.spent == 0
    finally:
        replay.close()
