import json
import httpx

from cetic.main import app
from tests.test_appgw import GW_ID, LISTENER_ID, TG_ID, ROUTE_ID


def test_disable_gateway_cache(runner, mock_api):
    route = mock_api.patch(f"/v1/app-gateways/{GW_ID}").mock(return_value=httpx.Response(200, json={}))
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--no-cache-enabled"])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {"cache_enabled": False}


def test_route_cache_create_and_disable(runner, mock_api):
    post = mock_api.post(f"/v1/app-gateways/{GW_ID}/routes").mock(return_value=httpx.Response(201, json={"id": ROUTE_ID}))
    result = runner.invoke(app, ["appgw", "route", "create", GW_ID, "--listener-id", LISTENER_ID,
        "--target-group-id", TG_ID, "--cache-mode", "force", "--cache-ttl", "90", "--cache-bypass-cookie", "session"])
    assert result.exit_code == 0, result.output
    assert json.loads(post.calls.last.request.content)["cache_default_ttl_s"] == 90
    patch = mock_api.patch(f"/v1/app-gateways/{GW_ID}/routes/{ROUTE_ID}").mock(return_value=httpx.Response(200, json={"id": ROUTE_ID}))
    result = runner.invoke(app, ["appgw", "route", "update", GW_ID, "--route-id", ROUTE_ID,
        "--cache-mode", "off", "--no-cache-debug-headers", "--cache-bypass-cookie", ""])
    assert result.exit_code == 0, result.output
    assert json.loads(patch.calls.last.request.content) == {"cache_mode": "off", "cache_debug_headers": False, "cache_bypass_cookies": []}


def test_cache_show_and_purge(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/cache").mock(return_value=httpx.Response(200, json={"appgw_id": GW_ID, "replicas": []}))
    post = mock_api.post(f"/v1/app-gateways/{GW_ID}/cache/purge").mock(return_value=httpx.Response(202, json={"id": "op", "status": "pending"}))
    assert runner.invoke(app, ["appgw", "cache", "show", GW_ID]).exit_code == 0
    result = runner.invoke(app, ["appgw", "cache", "purge", GW_ID])
    assert result.exit_code == 0, result.output
    assert post.called
    assert "pending" in result.output


def test_gateway_plan_change_sends_only_requested_fields(runner, mock_api):
    route = mock_api.patch(f"/v1/app-gateways/{GW_ID}").mock(
        return_value=httpx.Response(200, json={"plan": "appgw-medium", "cache_size_mb": 256})
    )
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--plan", "appgw-medium"])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {"plan": "appgw-medium"}


def test_create_cache_uses_server_profile_without_dimensions(runner, mock_api):
    route = mock_api.post("/v1/app-gateways").mock(
        return_value=httpx.Response(201, json={"id": GW_ID, "cache_size_mb": 256})
    )
    result = runner.invoke(app, ["appgw", "create", "--name", "web", "--region", "RNN",
        "--plan", "appgw-medium", "--vpc", "vpc", "--vnet", "vnet", "--cache-enabled"])
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert body["cache_enabled"] is True
    assert body["plan"] == "appgw-medium"
    assert "cache_size_mb" not in body
    assert "cache_max_object_kb" not in body


def test_freeform_dimensions_are_rejected_without_api_call(runner, mock_api):
    for option in ["--cache-size-mb", "--cache-max-object-kb"]:
        result = runner.invoke(app, ["appgw", "update", GW_ID, option, "64"])
        assert result.exit_code == 2, result.output
    assert not mock_api.calls


def test_cache_show_preserves_api_profile_json(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    profile = {"appgw_id": GW_ID, "cache_size_mb": 192, "cache_max_object_kb": 384,
        "cache_effective_size_mb": 192, "replicas": []}
    mock_api.get(f"/v1/app-gateways/{GW_ID}/cache").mock(
        return_value=httpx.Response(200, json=profile)
    )
    result = runner.invoke(app, ["appgw", "cache", "show", GW_ID])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == profile
