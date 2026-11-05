"""Policy transport: old API refusal, strict files and optimistic revisions."""
import json

import pytest

from cetic.main import app
from tests.test_appgw import GW_ID, LISTENER_ID, ROUTE_ID, TG_ID, VNET_ID, VPC_ID

POLICY = {"enabled": True, "policies": [{"key": "partners", "required_headers": [{"key": "id", "name": "x-partner", "op": "eq", "values": ["demo"]}]}]}


@pytest.fixture
def policy_file(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(POLICY))
    return path


def capabilities(mock_api, versions=None):
    return mock_api.get("/v1/platform/capabilities").respond(200, json={"appgw_security_policy_versions": [1] if versions is None else versions})


@pytest.mark.parametrize("raw", [b"null", b"[]", b'{"enabled":true,"enabled":false}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{"x":-1e999}', b'\xff', b'{"value":"\\ud800"}', b'{"\\udfff":"value"}', b' ' * 65537, b'{"secret":"DO_NOT_PRINT"'])
def test_invalid_file_never_sent_or_exposed(tmp_path, raw, runner, mock_api):
    path = tmp_path / "policy.json"
    path.write_bytes(raw)
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--l7-policy-file", str(path)])
    assert result.exit_code == 1
    assert not mock_api.calls
    assert "DO_NOT_PRINT" not in result.output


@pytest.mark.parametrize("versions", [[], [True], ["1"], [2], None])
def test_unsupported_api_never_receives_policy(versions, policy_file, runner, mock_api):
    mock_api.get("/v1/platform/capabilities").respond(200, json={"appgw_security_policy_versions": versions})
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--l7-policy-file", str(policy_file)])
    assert result.exit_code == 1
    assert len(mock_api.calls) == 1


@pytest.mark.parametrize("kind", ["gateway", "listener", "route"])
def test_update_gets_revision_and_explicit_clear(kind, runner, mock_api):
    capabilities(mock_api)
    mock_api.get(f"/v1/app-gateways/{GW_ID}").respond(200, json={"access_policy_generation": 7})
    path = f"/v1/app-gateways/{GW_ID}"
    args = ["appgw", "update", GW_ID]
    if kind != "gateway":
        child = LISTENER_ID if kind == "listener" else ROUTE_ID
        path += f"/{kind}s/{child}"
        args = ["appgw", kind, "update", GW_ID, f"--{kind}-id", child]
    sent = mock_api.patch(path).respond(200, json={"id": GW_ID, "l7_policy": {"enabled": False}, "security_revision": 8})
    result = runner.invoke(app, args + ["--clear-l7-policy"])
    assert result.exit_code == 0, result.output
    assert json.loads(sent.calls.last.request.content) == {"l7_policy": {}, "security_revision": 7}
    assert "enregistrée" in result.output


def test_stale_revision_is_not_retried(policy_file, runner, mock_api):
    capabilities(mock_api)
    mock_api.get(f"/v1/app-gateways/{GW_ID}").respond(200, json={"access_policy_generation": 7})
    write = mock_api.patch(f"/v1/app-gateways/{GW_ID}").respond(409, json={"detail": "revision"})
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--l7-policy-file", str(policy_file)])
    assert result.exit_code == 1
    assert write.call_count == 1


@pytest.mark.parametrize("response", [{"id": GW_ID}, {"l7_policy": {"enabled": False}, "security_revision": 1}, {"l7_policy": POLICY, "security_revision": True}])
def test_silent_drop_never_reports_success(response, policy_file, runner, mock_api):
    capabilities(mock_api)
    mock_api.get(f"/v1/app-gateways/{GW_ID}").respond(200, json={"access_policy_generation": 0})
    mock_api.patch(f"/v1/app-gateways/{GW_ID}").respond(200, json=response)
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--l7-policy-file", str(policy_file)])
    assert result.exit_code == 1
    assert "mise à jour" not in result.output


def test_omission_does_not_clear_or_negotiate(runner, mock_api):
    write = mock_api.patch(f"/v1/app-gateways/{GW_ID}").respond(200, json={"id": GW_ID})
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--plan", "medium"])
    assert result.exit_code == 0, result.output
    assert json.loads(write.calls.last.request.content) == {"plan": "medium"}
    assert len(mock_api.calls) == 1


@pytest.mark.parametrize("kind", ["gateway", "listener", "route"])
def test_creation_transports_policy(kind, policy_file, runner, mock_api):
    capabilities(mock_api)
    path = "/v1/app-gateways"
    args = ["appgw", "create", "--name", "gw", "--region", "RNN", "--plan", "small", "--vpc", VPC_ID, "--vnet", VNET_ID]
    if kind == "listener":
        path += f"/{GW_ID}/listeners"
        args = ["appgw", "listener", "add", GW_ID, "--hostname", "example.test"]
    elif kind == "route":
        path += f"/{GW_ID}/routes"
        args = ["appgw", "route", "create", GW_ID, "--listener-id", LISTENER_ID, "--target-group-id", TG_ID]
    write = mock_api.post(path).respond(201, json={"id": GW_ID, "replica_count": 2, "l7_policy": POLICY, "security_revision": 1})
    result = runner.invoke(app, args + ["--l7-policy-file", str(policy_file)])
    assert result.exit_code == 0, result.output
    assert json.loads(write.calls.last.request.content)["l7_policy"] == POLICY


def test_clear_and_file_are_exclusive(policy_file, runner, mock_api):
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--l7-policy-file", str(policy_file), "--clear-l7-policy"])
    assert result.exit_code == 1
    assert not mock_api.calls


def test_enabled_but_different_policy_is_not_confirmed(policy_file, runner, mock_api):
    capabilities(mock_api)
    mock_api.get(f"/v1/app-gateways/{GW_ID}").respond(200, json={"access_policy_generation": 0})
    mock_api.patch(f"/v1/app-gateways/{GW_ID}").respond(200, json={"security_revision": 1, "l7_policy": {"enabled": True, "policies": []}})
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--l7-policy-file", str(policy_file)])
    assert result.exit_code == 1
    assert "mise à jour" not in result.output


def test_canonical_network_and_defaults_are_confirmed(tmp_path, runner, mock_api):
    policy = tmp_path / "l4.json"
    policy.write_text(json.dumps({"enabled": True, "tcp_443": {"allow_cidrs": ["192.0.2.8/24", "192.0.2.0/24"]}}))
    capabilities(mock_api)
    mock_api.get(f"/v1/app-gateways/{GW_ID}").respond(200, json={"access_policy_generation": 0})
    mock_api.patch(f"/v1/app-gateways/{GW_ID}").respond(200, json={"security_revision": 1, "l4_policy": {"enabled": True, "tcp_80": {"allow_cidrs": [], "deny_cidrs": []}, "tcp_443": {"allow_cidrs": ["192.0.2.0/24"], "deny_cidrs": []}}})
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--l4-policy-file", str(policy)])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("clear", [True, False])
def test_disabled_activation_still_allows_clear(clear, policy_file, runner, mock_api):
    mock_api.get("/v1/platform/capabilities").respond(200, json={"appgw_security_policy_versions": [1], "appgw_security_policies_enabled": False})
    mock_api.get(f"/v1/app-gateways/{GW_ID}").respond(200, json={"access_policy_generation": 2})
    write = mock_api.patch(f"/v1/app-gateways/{GW_ID}").respond(200, json={"security_revision": 3, "l7_policy": {"enabled": False, "policies": []}})
    args = ["--clear-l7-policy"] if clear else ["--l7-policy-file", str(policy_file)]
    result = runner.invoke(app, ["appgw", "update", GW_ID, *args])
    assert result.exit_code == (0 if clear else 1), result.output
    assert write.called is clear


def test_valid_unicode_surrogate_pair_is_preserved(tmp_path, runner, mock_api):
    policy = tmp_path / "unicode-policy.json"
    value = {"enabled": True, "policies": [{"key": "unicode", "scope": {
        "path": {"op": "exact", "value": "/🚀"}}, "allow_rules": [
        {"key": "read", "match": {"methods": ["GET"]}}]}]}
    # ensure_ascii emits a valid UTF-16 surrogate pair, unlike the rejected
    # isolated escapes. Both representations describe the same UTF-8 path.
    policy.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
    capabilities(mock_api)
    mock_api.get(f"/v1/app-gateways/{GW_ID}").respond(200, json={"access_policy_generation": 0})
    response_policy = json.loads(json.dumps(value))
    response_policy["policies"][0]["scope"]["path"]["value"] = "/%F0%9F%9A%80"
    write = mock_api.patch(f"/v1/app-gateways/{GW_ID}").respond(
        200, json={"security_revision": 1, "l7_policy": response_policy})
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--l7-policy-file", str(policy)])
    assert result.exit_code == 0, result.output
    assert json.loads(write.calls.last.request.content)["l7_policy"] == value


@pytest.mark.parametrize("kind", ["gateway", "listener", "route"])
@pytest.mark.parametrize("status", [403, 409, 422, 500])
def test_policy_errors_never_echo_server_values(kind, status, policy_file, runner, mock_api):
    capabilities(mock_api)
    mock_api.get(f"/v1/app-gateways/{GW_ID}").respond(200, json={"access_policy_generation": 7})
    path = f"/v1/app-gateways/{GW_ID}"
    args = ["appgw", "update", GW_ID]
    if kind != "gateway":
        child = LISTENER_ID if kind == "listener" else ROUTE_ID
        path += f"/{kind}s/{child}"
        args = ["appgw", kind, "update", GW_ID, f"--{kind}-id", child]
    write = mock_api.patch(path).respond(status, json={"detail": "invalid header value DO_NOT_PRINT"})
    result = runner.invoke(app, args + ["--l7-policy-file", str(policy_file)])
    assert result.exit_code == 1
    assert "DO_NOT_PRINT" not in result.output
    assert "Erreur" in result.output
    assert write.call_count == 1


def test_ordinary_update_preserves_business_diagnostic(runner, mock_api):
    mock_api.patch(f"/v1/app-gateways/{GW_ID}").respond(422, json={"detail": "Plan indisponible dans cette région"})
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--plan", "medium"])
    assert result.exit_code == 1
    assert "Plan indisponible dans cette région" in result.output
