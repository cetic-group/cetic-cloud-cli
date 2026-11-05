"""Single-instance creation, immutable CLI interface and old-server detection."""
import json

import httpx
import pytest

from cetic.main import app


@pytest.mark.parametrize("service,path", [("appgw", "/v1/app-gateways"), ("lb", "/v1/load-balancers")])
@pytest.mark.parametrize("count", [None, 1, 2])
def test_create_replica_count(runner, mock_api, service, path, count):
    requested = 2 if count is None else count
    captured = {}

    def respond(request):
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={"id": "created-1", "replica_count": requested})

    mock_api.post(path).mock(side_effect=respond)
    args = [service, "create", "--name", "dev", "--region", "RNN", "--vnet", "vnet-1"]
    if service == "appgw":
        args += ["--vpc", "vpc-1"]
    if count is not None:
        args += ["--replica-count", str(count)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert captured["replica_count"] == requested


@pytest.mark.parametrize("service", ["appgw", "lb"])
@pytest.mark.parametrize("count", ["0", "3", "1.5"])
def test_rejects_invalid_count_without_api_call(runner, mock_api, service, count):
    args = [service, "create", "--name", "dev", "--region", "RNN", "--vnet", "v", "--replica-count", count]
    if service == "appgw":
        args += ["--vpc", "v"]
    assert runner.invoke(app, args).exit_code != 0
    assert not mock_api.calls


@pytest.mark.parametrize("service,path", [("appgw", "/v1/app-gateways"), ("lb", "/v1/load-balancers")])
def test_legacy_api_ignoring_single_shows_created_id(runner, mock_api, service, path):
    mock_api.post(path).mock(return_value=httpx.Response(201, json={"id": "keep-created-id"}))
    args = [service, "create", "--name", "dev", "--region", "RNN", "--vnet", "v", "--replica-count", "1"]
    if service == "appgw":
        args += ["--vpc", "v"]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "keep-created-id" in result.output
    assert "n'a pas appliqué" in result.output


@pytest.mark.parametrize("service", ["appgw", "lb"])
def test_update_has_no_mutable_replica_option(runner, service):
    result = runner.invoke(app, [service, "update", "existing", "--replica-count", "1"])
    assert result.exit_code != 0
