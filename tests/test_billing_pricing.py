"""Un prix mensuel absent ne doit pas faire tomber la commande entière.

L'API cesse d'inventer un prix mensuel pour les dimensions facturées **à
l'unité consommée** (un Go sorti, une requête RPC). Un prix mensuel y suppose
un volume mensuel que l'API ne connaît pas ; le ×730 d'avant annonçait
**7,30 € le Go** d'egress facturé **0,01 €**. Elle renvoie donc `null` dans
`monthly_price_eur` / `yearly_price_eur` / `effective_monthly_price_eur`, et
laisse le prix réel dans `hourly_price_cents`, à interpréter via
`billing_dimension` exposé à côté.

Le CLI, lui, formatait ces trois champs en `:.2f` **sans garde**. Un `None`
n'y dégrade pas une ligne : le formatage vit à l'intérieur de la compréhension
de liste qui construit le tableau, donc une seule ligne d'egress fait échouer
la commande **en entier**, avec une trace Python brute à la place du tableau.

Et ce n'est pas un cas limite : les lignes à dimension unitaire existent en
production (`object_egress` en `per_gb_egress`, `blockchain_rpc_requests` en
`per_request`), donc elles sont dans la grille que `cetic billing pricing`
parcourt — la commande la plus banale du lot.

Ces tests exercent les commandes de bout en bout (`CliRunner` + `respx`) sur
une grille qui contient une ligne horaire **et** une ligne à l'unité : c'est le
mélange qui est réaliste, et c'est lui qui plantait.

Cf. cetic-group/cetic-cloud-platform#1085, #1094.
"""
from __future__ import annotations

from typing import Any

import httpx

from cetic.main import app


def _ligne_horaire() -> dict[str, Any]:
    return {
        "resource_type": "container",
        "plan": "nano",
        "hourly_price_cents": 5.0,
        "monthly_price_eur": 36.5,
        "yearly_price_eur": 438.0,
        "billing_dimension": "flat_hourly",
        "currency": "eur",
        "description": "Container nano",
        "is_free": False,
        "stopped_disk_price_cents_per_gb_hour": None,
    }


def _ligne_a_l_unite() -> dict[str, Any]:
    """L'egress objet tel que la grille le porte : 1 centime le Go, pas de mensuel."""
    return {
        "resource_type": "object_egress",
        "plan": None,
        "hourly_price_cents": 1.0,
        "monthly_price_eur": None,
        "yearly_price_eur": None,
        "billing_dimension": "per_gb_egress",
        "currency": "eur",
        "description": "Sortie de données",
        "is_free": False,
        "stopped_disk_price_cents_per_gb_hour": None,
    }


def test_pricing_survit_a_une_ligne_sans_prix_mensuel(runner, mock_api) -> None:
    """`cetic billing pricing` — ROUGE avant le correctif (TypeError non rattrapée).

    La commande la plus banale du lot, sur la route publique. Une seule ligne
    d'egress suffisait à ne rien afficher du tout.
    """
    mock_api.get("/v1/billing/pricing").mock(
        return_value=httpx.Response(200, json=[_ligne_horaire(), _ligne_a_l_unite()])
    )

    res = runner.invoke(app, ["billing", "pricing"])

    assert res.exit_code == 0, res.output
    assert "36.50 €" in res.output, "la ligne horaire garde son prix mensuel"
    assert "0.01 € / unité" in res.output, (
        "le prix unitaire remplace le mensuel absent — un tiret ferait "
        "disparaître une information qui existe"
    )


def test_pricing_v2_survit_aussi(runner, mock_api) -> None:
    """`cetic billing pricing --v2` — le garde `is_free` ne protège de rien ici.

    Une ligne à dimension unitaire est payante : `is_free` vaut `False`, et on
    tombe droit sur le `:.2f`.
    """
    mock_api.get("/v1/billing/pricing-v2").mock(
        return_value=httpx.Response(200, json=[_ligne_horaire(), _ligne_a_l_unite()])
    )

    res = runner.invoke(app, ["billing", "pricing", "--v2"])

    assert res.exit_code == 0, res.output
    assert "0.01 € / unité" in res.output


def test_estimate_survit_a_un_mensuel_nul(runner, mock_api) -> None:
    """`cetic billing estimate` — trois champs nullables sur le même écran."""
    mock_api.get("/v1/billing/estimate").mock(return_value=httpx.Response(200, json={
        "resource_type": "object_egress",
        "plan": None,
        "hourly_price_cents": 1.0,
        "monthly_price_eur": None,
        "yearly_price_eur": None,
        "effective_monthly_price_eur": None,
        "billing_dimension": "per_gb_egress",
        "is_free": False,
        "commit_discount_pct": 0,
        "promo_discount_pct": 0,
        "free_tier_units_remaining": 0,
    }))

    res = runner.invoke(app, ["billing", "estimate", "object_egress"])

    assert res.exit_code == 0, res.output
    assert "0.01 € / unité" in res.output


def test_un_prix_sub_centime_garde_ses_decimales(runner, mock_api) -> None:
    """Arrondir à deux décimales afficherait « 0.00 € » pour un prix réel.

    Le stockage est en `NUMERIC(10,4)` précisément pour ces tarifs-là.
    """
    ligne = _ligne_a_l_unite()
    ligne["hourly_price_cents"] = 0.0014
    mock_api.get("/v1/billing/pricing").mock(
        return_value=httpx.Response(200, json=[ligne])
    )

    res = runner.invoke(app, ["billing", "pricing"])

    assert res.exit_code == 0, res.output
    assert "0.00 €" not in res.output, "un prix réel ne doit pas s'afficher comme gratuit"
    assert "0.000014" in res.output.replace(" ", "") or "0.0000" in res.output


def test_commit_create_accepte_les_quatre_durees(runner, mock_api) -> None:
    """Les engagements 2 et 3 ans étaient inatteignables depuis le CLI.

    L'API les accepte ; le CLI les rejetait avant même d'appeler l'API.
    """
    mock_api.post("/v1/billing/commits").mock(return_value=httpx.Response(200, json={
        "id": "c1", "commit_type": "three_years", "discount_pct": 30,
        "start_at": "2026-08-20T00:00:00Z", "end_at": "2029-08-20T00:00:00Z",
    }))

    res = runner.invoke(app, ["billing", "commit", "create", "three_years"])

    assert res.exit_code == 0, res.output


def test_commit_list_n_affiche_pas_trois_ans_comme_mensuel(runner, mock_api) -> None:
    """Le repli binaire affichait « Mensuel » pour un engagement de trois ans.

    C'est une information fausse, et rien dans l'affichage ne permet de s'en
    apercevoir : un type inconnu doit s'afficher tel quel, jamais se déguiser.
    """
    mock_api.get("/v1/billing/commits").mock(return_value=httpx.Response(200, json=[{
        "id": "c1", "commit_type": "three_years", "discount_pct": 30,
        "start_at": "2026-08-20T00:00:00Z", "end_at": "2029-08-20T00:00:00Z",
        "canceled_at": None,
    }]))

    res = runner.invoke(app, ["billing", "commit", "list"])

    assert res.exit_code == 0, res.output
    assert "Mensuel" not in res.output, (
        "un engagement de trois ans à -30 % affiché « Mensuel » est un mensonge"
    )
    assert "3 ans" in res.output or "ans" in res.output
