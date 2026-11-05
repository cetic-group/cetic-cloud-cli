"""Tarifs single/HA et cache : restituer les montants du serveur sans les dériver."""
import json

import httpx
import pytest

from cetic.main import app


def component(monthly, hourly, free=False):
    return {"monthly_price_eur": monthly, "hourly_price_cents": hourly, "is_free": free, "currency": "eur"}


@pytest.mark.parametrize("kind", ["appgw", "lb"])
def test_plans_show_explicit_mode_totals_and_missing_prices(runner, mock_api, monkeypatch, kind):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/compute/plans").mock(return_value=httpx.Response(200, json=[{
        "key": f"{kind}-custom", "price_eur_month": 999,
        "appliance_pricing": [
            {"replica_count": 1, "base": component(4.27, 0.585), "cache": None},
            {"replica_count": 2, "base": component(7.83, 1.073), "cache": component(0, 0, True)},
        ],
    }]))
    result = runner.invoke(app, [kind, "plans"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["replica_count"] for row in rows] == [1, 2]
    assert [row["prix_mois"] for row in rows] == [4.27, 7.83]
    assert rows[0]["cache_prix_mois"] is None
    assert rows[1]["cache_prix_mois"] == 0
    assert rows[1]["cache_is_free"] is True


@pytest.mark.parametrize("variants", [[], None])
def test_missing_catalog_price_does_not_fall_back_to_indicative_price(runner, mock_api, monkeypatch, variants):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/compute/plans").mock(return_value=httpx.Response(200, json=[{
        "key": "appgw-small", "price_eur_month": 999, "appliance_pricing": variants,
    }]))
    result = runner.invoke(app, ["appgw", "plans"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["prix_mois"] is None


def test_old_catalog_does_not_invent_cache_or_mode(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/compute/plans").mock(return_value=httpx.Response(200, json=[{
        "key": "appgw-small", "price_eur_month": 7.99,
    }]))
    result = runner.invoke(app, ["appgw", "plans"])
    row = json.loads(result.output)[0]
    assert row["prix_mois"] == 7.99
    assert row["replica_count"] is None
    assert row["cache_prix_mois"] is None


def estimate_response(**updates):
    return {"resource_type": "appgw", "plan": "small", "hourly_price_cents": 1,
            "monthly_price_eur": 10.23, "yearly_price_eur": 122.76,
            "is_free": False, "commit_discount_pct": 0, "promo_discount_pct": 0,
            "effective_monthly_price_eur": 10.23, "free_tier_units_remaining": 0, **updates}


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("cache", [False, True])
def test_estimate_sends_axes_and_prints_server_breakdown(runner, mock_api, count, cache):
    captured = {}

    def respond(request):
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=estimate_response(replica_count=count, cache_enabled=cache,
            base_monthly_price_eur=8.19, cache_monthly_price_eur=2.04 if cache else 0))

    mock_api.get("/v1/billing/estimate").mock(side_effect=respond)
    result = runner.invoke(app, ["billing", "estimate", "appgw", "--plan", "custom&safe",
                                "--replica-count", str(count), "--cache-enabled" if cache else "--no-cache-enabled"])
    assert result.exit_code == 0, result.output
    assert captured == {"resource_type": "appgw", "plan": "custom&safe", "replica_count": str(count), "cache_enabled": str(cache).lower()}
    assert "Base : 8.19" in result.output
    assert ("Option cache : 2.04" if cache else "Option cache : 0.00") in result.output
    assert "10.23" in result.output  # returned total, not recomputed from the components


@pytest.mark.parametrize("args", [["--replica-count", "1"], ["--cache-enabled"]])
def test_estimate_rejects_old_server_ignoring_requested_axes(runner, mock_api, args):
    mock_api.get("/v1/billing/estimate").mock(return_value=httpx.Response(200, json=estimate_response()))
    result = runner.invoke(app, ["billing", "estimate", "appgw", *args])
    assert result.exit_code == 1
    assert "n'a pas confirmé" in result.output
    assert "10.23" not in result.output


def test_old_estimate_still_supports_historical_default(runner, mock_api):
    mock_api.get("/v1/billing/estimate").mock(return_value=httpx.Response(200, json=estimate_response()))
    result = runner.invoke(app, ["billing", "estimate", "appgw"])
    assert result.exit_code == 0
    assert "10.23" in result.output


def test_pricing_v2_keeps_replica_count_and_cache_addon(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/billing/pricing-v2").mock(return_value=httpx.Response(200, json=[
        {"resource_type": "appgw_cache", "plan": "small", "replica_count": n,
         "hourly_price_cents": 0.1 * n, "monthly_price_eur": 0.73 * n, "yearly_price_eur": 8.76 * n}
        for n in [1, 2]
    ]))
    result = runner.invoke(app, ["billing", "pricing", "--v2"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["replica_count"] for row in rows] == [1, 2]
    assert all(row["type"] == "appgw_cache" for row in rows)


def test_unavailable_combination_surfaces_server_error(runner, mock_api):
    mock_api.get("/v1/billing/estimate").mock(return_value=httpx.Response(409, json={
        "detail": "appliance_pricing_unavailable",
    }))
    result = runner.invoke(app, ["billing", "estimate", "appgw", "--replica-count", "1", "--cache-enabled"])
    assert result.exit_code == 1
    assert "appliance_pricing_unavailable" in result.output
