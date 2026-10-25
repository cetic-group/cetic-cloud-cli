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
