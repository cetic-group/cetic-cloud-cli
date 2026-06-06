# cetic-cloud-cli — CLAUDE.md

> CLI officielle `cetic` pour CETIC Cloud Platform.
> Source de vérité backend : `cetic-cloud-platform/apps/api/` (schémas Pydantic + routes).
> Repo public : https://github.com/cetic-group/cetic-cloud-cli

---

## Stack

Python 3.12 · Typer + Rich · httpx · keyring · pytest + respx (mock HTTP).
Binaire `cetic` distribué via PyInstaller (GitHub Actions sur tag `v*`, 6 plateformes).

## Layout

```
cetic/
  main.py           ← app Typer racine, add_typer() de chaque sous-commande
  client.py         ← get/post/patch/put/delete vers l'API (CCP_API_KEY/CCP_API_URL)
  config.py         ← config fichier + env (CCP_REGION, CCP_OUTPUT, CCP_LANG)
  commands/
    _render.py      ← render_list/render_one/render_table (table|json|yaml selon CCP_OUTPUT)
    ip.py, lb.py, appgw.py, vm.py, container.py, k8s.py, db.py, …  ← 1 fichier par domaine
tests/
  conftest.py       ← fixtures runner (CliRunner), mock_api (respx), mock_keyring, cfg_env
  test_<domaine>.py ← 1 fichier par commande, pattern respx + capture du body envoyé
```

## Conventions code

- **Alignement strict sur le contrat backend** : chaque body envoyé doit matcher les champs des
  schémas Pydantic de `apps/api/app/schemas/*.py` et chaque path une route réelle de
  `apps/api/app/api/v1/*.py`. Ne JAMAIS inventer un champ ou supposer un endpoint —
  cf. l'incident `custom_domain` (flag CLI envoyé pendant des semaines, silencieusement
  ignoré par Pydantic → les certs ne s'émettaient jamais).
- Messages user en français, help strings en français. `rprint` (Rich) + `[green]✓[/green]` / `[red]Erreur : …[/red]`.
- Erreurs API : `except client.APIError as e: rprint(f"[red]Erreur : {e.detail}[/red]"); raise typer.Exit(1)`.
- Flags répétables pour les listes (`--tag`, `--backend`, `--acme-dns-credential KEY=VALUE`).
- Confirmations destructives : `--yes/-y` ou `typer.confirm(...)`.
- Helpers purs module-level (parsing de specs type `container:UUID:PORT[:WEIGHT]`) → testables unitairement.
- Pas de jargon infra dans les sorties (HAProxy/LXC/certbot interdits — dire « certificat Let's Encrypt », « load balancer »).

## Tests

- `python -m pytest tests/ -q` — tout doit être vert avant release.
- Pattern : fixture `mock_api` (respx) + handler qui capture `json.loads(request.content)` pour
  asserter le body exact envoyé, + assert sur `result.output`.
- Les tests d'enregistrement des sous-apps assertent sur `main_app.registered_groups[].name`
  (PAS `typer_instance.info.name`, qui n'est jamais set — bug historique des tests iam/service-account).

## Convention release

1. Bump **les 2 fichiers** : `cetic/__init__.py` (`__version__`) ET `pyproject.toml` (`version`).
2. Mettre à jour le README si des commandes/flags changent (sections guide + cheat sheet).
3. PR → merge squash (`gh pr merge --squash --delete-branch`).
4. Tag : `git tag -a vX.Y.Z -m "…" && git push origin vX.Y.Z` → GitHub Actions build les
   binaires PyInstaller (~3 min) et crée la release.
5. SemVer : minor = nouvelle commande/flag ; patch = fix. Un flag retiré = breaking → noter `!`
   dans le commit (on tolère le retrait en minor si le flag était non-fonctionnel).

## Versions

**Latest : `v0.22.0`** (2026-06-06)

- `v0.22.0` — feat : commandes de catalogue compute (plans + templates + templates
  custom) calquées sur `cetic db <engine> plans`. Helper partagé
  `commands/_catalog.py` (compute_plans/templates LXC/QEMU/custom). `cetic container
  plans|templates|custom-templates`, `cetic vm plans|templates|custom-templates`,
  `cetic scale-set plans|templates`, `cetic vm-scale-set plans|templates`, `cetic
  k8s plans|versions|templates`. Endpoints backend : `GET /v1/compute/plans`
  (`?kind=container|vm|k8s_node`), `/v1/templates`, `/v1/qemu-templates`,
  `/v1/custom-templates` (filtré client-side par `template_type`), `/v1/k8s/templates`.
  Plans compute partagés VM/container → factorisés mais exposés par sous-app.
- `v0.21.0` — feat : `vpc create --cidr` + colonne CIDR (cascade VPC CIDR block CCP v2.12.0).
- `v0.20.0` — feat : fichier de config déplacé vers `~/.ccp/config` (TOML, migration
  auto depuis `~/.config/cetic/config.toml` au 1er run) ; header `X-CCP-Client: cli` +
  `User-Agent: cetic-cli/<version>` sur toutes les requêtes (alimente l'audit trail
  plateforme) ; fix `config view` (préfixe `CCP_` au lieu de `CL_` pour détecter la
  source env). PR #14.
- `v0.19.1` — fix : `registry repos --all` double-encodait le curseur de pagination (`%2F` → `%252F`) ;
  fix tests iam/service-account (assertion sur registered_groups). PR #13.
- `v0.19.0` — feat : `ip allocate --label/--description/--quantity` (batch 1-8) + `ip update` + colonne
  Nom + fix `container_id` ; `lb create` listeners HTTPS + Let's Encrypt (`--listener-protocol/--listener-port/
  --domain/--acme-challenge/--acme-dns-provider/--acme-dns-credential/--backend`) + `lb acme-providers` +
  `lb acme-retry` + `lb backend add/update/remove` ; `appgw listener add --acme-challenge` (retrait
  `--custom-domain` no-op). PR #12. Cascade : provider v4.1.0, modules v0.23.0.
- `v0.18.x` — colonnes non tronquées en table, UUID complets, alignements docs.
- `v0.17.0` — `k8s create --tier` + `kubeconfig --mode` (CCKS HA).
- `v0.16.0` — `key add --scope` (SSH key scoping v2).

## Variables d'environnement

`CCP_API_KEY` (auth) · `CCP_API_URL` (défaut prod, override dev only) · `CCP_REGION` · `CCP_OUTPUT` (table|json|yaml) · `CCP_LANG`
