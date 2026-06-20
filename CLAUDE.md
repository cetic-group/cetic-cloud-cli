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

**Latest : `v0.34.6`**

- `v0.34.6` — 2 fixes `cetic k8s` : **(1) `pool scale` renvoyait `Not Found`**
  — il POSTait sur `/v1/k8s/clusters/{id}/node-pools/{pid}/scale`, route qui
  **n'existe pas** côté backend (le scaling passe par le **PATCH** du node pool
  avec `replicas`, cf. `k8s_clusters.py` : seuls GET/POST/PATCH/DELETE sur
  `/node-pools[/{pid}]`). Aligné sur `pool update`. **(2) anti-leak `k8s get`**
  — les champs `proxy_secondary_vmid` / `proxy_secondary_node` / `proxy_vip_vnet`
  (frontal HA interne) étaient exposés au client ; `get` retire désormais **tous
  les champs `proxy_*`** de la sortie (table ET JSON/YAML) — le proxy est un
  service interne, le client n'en a aucune visibilité. Tests : `pool scale`
  (PATCH+replicas) + `get` masque proxy_* (test inversé).
- `v0.34.5` — fix : `cetic org switch` ne **switchait pas réellement**
  (affichait « ✓ Org active mise à jour » mais restait sur l'org par défaut).
  Cause = désalignement de contrat : le backend `SwitchOrgRequest` attend
  **`target_org_id`** (`apps/api/app/api/v1/auth.py`), la CLI envoyait `org_id`
  → Pydantic ignore le champ inconnu → `target_org_id=None` → le backend renvoie
  un token scopé sur l'**org par défaut** (`if req.target_org_id is None: return
  default_org`). Vérifié live (token post-switch portait l'`active_org_id` de
  l'org par défaut, pas celle demandée). Fix = envoyer `{"target_org_id": …}` +
  **garde-fou** : si `res.active_org_id` ≠ org demandée, on affiche un ⚠ au lieu
  du ✓ trompeur. (Le fix v0.34.4 `config.set` → `set_value` était nécessaire
  mais ne suffisait pas — il persistait le token de la mauvaise org.)
- `v0.34.4` — fix : `cetic org switch` plantait avec
  `AttributeError: module 'cetic.config' has no attribute 'set'`. La commande
  appelait `config.set("api_key", …)` alors que la fonction de persistance
  s'appelle `config.set_value(...)` (cf. `cetic/config.py`). Le `switch`
  n'enregistrait donc jamais le nouveau JWT (`active_org_id`) côté config locale.
  Corrigé en `config.set_value(...)`. + **`-h` alias de `--help`** partout
  (`context_settings={"help_option_names": ["-h", "--help"]}` sur le Typer
  racine ; Click propage aux sous-commandes). `-v`/`--version` déjà présent
  (callback racine). Nouveau `tests/test_org.py` (régression `switch` +
  alias `-h`/`-v`) — la commande `switch` n'avait aucun test.
- `v0.34.3` — fix : tri par défaut de `cetic k8s templates` = **nom (OS)
  croissant PRIMAIRE** puis **version décroissante** au sein de chaque OS
  (regroupe par famille OS, majeure en haut dans chaque groupe). v0.34.1 avait
  la version en clé primaire ; le nom (`os_label`) est désormais la clé primaire.
- `v0.34.2` — feat : `cetic k8s templates` affiche les **deux tags PVE** que
  CAPMOX matche en ET (#460) : colonne **`Clé (version)`** (`kube-v<ver>`,
  =`os_key`) + nouvelle colonne **`Clé (OS)`** (`ccks-os-<slug>`, dérivée de
  `os`). Depuis le multi-OS, `kube-v<ver>` n'est plus unique → un template est
  identifié par le couple (version, OS). La colonne « OS (slug) » redondante est
  retirée (l'info est dans `Clé (OS)`). `—` si le template n'a pas d'OS (legacy).
- `v0.34.1` — fix : tri par défaut de `cetic k8s templates`. Clé primaire =
  **version Kubernetes décroissante** (majeure en haut, inchangé), clé
  secondaire = **nom croissant** (a→z) à version égale (avant : `os_key`
  décroissant, qui paraissait non trié). Double passe stable (nom puis version).
- `v0.34.0` — feat : fixes/ajouts `cetic ssh` + `scp` + `auth login --sso` +
  filtres `k8s templates` (issue cetic-cloud-platform#488). **(1) `cetic ssh`
  sélection du bastion par VPC** : `_resolve_bastion_host` prenait le **premier**
  bastion venu → échec « connexion à la cible impossible » quand la cible était
  dans un autre VPC que ce bastion (cause racine confirmée live). Désormais : si
  plusieurs bastions, on résout l'IP de la cible → VNet/VPC (via `GET /v1/vpcs`
  + `/v1/vpcs/{id}/vnets`) et on choisit le bastion qui **dessert ce VPC**
  (`vpc_ids`) ; erreur claire (demande `--bastion`) si on ne peut trancher.
  Login défaut inchangé (`root` — fonctionne sur VM Linux via
  `PermitRootLogin prohibit-password`). **(2) `cetic scp`** (nouveau, top-level) :
  transfert récursif via le bastion, format `TARGET:chemin` (upload/download),
  `-r` auto si la source locale est un répertoire ; réutilise le flux cert
  éphémère + passe la cible au bastion via `-o SetEnv=CCP_TARGET=<target>` (le
  sous-système sftp/scp ne porte pas d'argument `host=`). ⚠️ **requiert un
  rebuild du golden bastion** (le daemon `ccp-bastiond` lit désormais
  `CCP_TARGET` dans `resolveTarget`). **(3) `cetic auth login --sso github|google`** :
  flux loopback façon `gh` (serveur local éphémère + navigateur →
  `GET /v1/auth/oauth/{provider}/authorize?cli_redirect=http://127.0.0.1:<port>/...`
  → tokens). Requiert le support backend `cli_redirect` loopback (livré côté
  api). **(4) `cetic k8s templates --name/--k8s-version`** + tri par version k8s
  décroissante (majeure en haut). Tests : +20.
- `v0.33.0` — feat : **version Kubernetes par node pool** (control plane vs
  workers). `cluster.k8s_version` est désormais la version du **control plane** ;
  chaque node pool a une `k8s_version` optionnelle (`null` = hérite du control
  plane, doit être ≤ control plane sinon 422 backend). Nouveau flag
  **`--pool-version`** sur `cetic k8s create` → envoyé en
  `initial_pool.k8s_version` (omis = hérite). Nouveau flag **`--version`** sur
  `cetic k8s pool create` (→ `k8s_version` au POST) et `cetic k8s pool update`
  (→ `k8s_version` au PATCH, pin/upgrade du pool). `--version` de `cetic k8s
  create` documenté comme étant la version du **control plane**. Validation
  locale du format `vX.Y.Z` / `X.Y.Z` (helper module-level `_validate_k8s_version`,
  testable) avec erreur claire avant tout appel réseau. Colonne **Version**
  ajoutée à `cetic k8s pool list` : affiche la version pinée, ou
  `(héritée: <version CP>)` si `null` (la version du control plane est récupérée
  best-effort via un GET cluster ; fallback `(héritée)`). Helper d'affichage
  `_fmt_pool_version`. Aucun nouvel endpoint (les schémas
  create-cluster/create-pool/patch-pool acceptaient déjà `k8s_version`). Tests :
  10 nouveaux. Comportement `--os` inchangé.
- `v0.32.0` — feat : **multi-OS sur les clusters K8s** (issue
  cetic-cloud-platform#460). Flag **`--os flatcar|ubuntu|rocky9`** (défaut
  `flatcar`) sur `cetic k8s create` → envoie `os_image` dans le POST
  `/v1/k8s/clusters`. `--template` devient **optionnel** : si omis, le template
  est résolu automatiquement contre `GET /v1/k8s/templates?region=…` en matchant
  le triplet (`os`, `k8s_version`, `region`) et son `os_key` est envoyé en
  `os_template_key` (helper module-level `_resolve_os_template_key`, testable) ;
  erreur claire si aucun template ne matche (le backend re-valide → 422 sinon).
  Validation locale de `--os` contre les 3 slugs. Colonne **OS** (Flatcar /
  Ubuntu / Rocky Linux 9, depuis `os_image`) ajoutée à `cetic k8s list` et champ
  `os` à `cetic k8s get` (slug brut `os_image` conservé pour JSON/YAML). Colonne
  **OS (slug)** ajoutée à `cetic k8s templates` (champ `os` de la réponse).
  Helper d'affichage `_fmt_os`. Aucun nouvel endpoint.
- `v0.31.0` — feat : **Windows sur `cetic vm create` / `cetic vm-scale-set create`**
  (issue cetic-cloud-platform#446, alignement v2.28.x). Flag
  **`--windows-license-consent`** sur `vm create` et `vm-scale-set create` (envoie
  `windows_license_consent=true` ; obligatoire pour un template Windows `win-*` ou
  un template custom Windows, sinon 422 backend) + **validation locale de la
  complexité du mot de passe** quand le flag est posé (≥ 12 caractères, ≥ 3
  catégories — helper partagé `_compute.validate_windows_password`, aligné sur la
  politique backend). Colonne **OS** (Linux/Windows depuis `os_family`) ajoutée à
  `cetic vm list`, `cetic vm-scale-set list`, `cetic template list` et
  `cetic {vm,container} custom-templates`. Les VM Windows passent par `cetic vm`
  (VM QEMU native) : il n'y a **pas** de groupe `cetic windows` séparé (une
  ancienne tentative ciblant `/v1/windows-instances` — endpoint retiré de l'API en
  v2.27.0, abandon dockur — n'a jamais été mergée). Tests : 6 nouveaux (flag +
  rejet mot de passe faible).
- `v0.30.0` — feat : gestion des VPC couverts + IP publique pour le VPN et le
  Bastion (gaps d'audit d'alignement — endpoints backend déjà présents).
  **VPN** : `cetic vpn gateway vpc list|add|rm GATEWAY [VPC_ID]` (hot-plug,
  `GET/POST /v1/vpn/gateways/{id}/vpcs` body `{vpc_id}` + `DELETE
  /v1/vpn/gateways/{id}/vpcs/{vpc_id}`) ; `cetic vpn gateway attach-ip GATEWAY`
  + `detach-ip GATEWAY` (`POST /v1/vpn/gateways/{id}/{attach,detach}-ip` — **le
  endpoint attach-ip VPN ne lit aucun body** : il alloue auto une IP disponible
  de la région, donc pas de `--public-ip` côté VPN). **Bastion** : `cetic
  bastion vpc list|add|rm BASTION [VPC_ID]` (`GET/POST /v1/bastions/{id}/vpcs`
  + `DELETE .../vpcs/{vpc_id}`) ; `cetic bastion attach-ip BASTION
  [--public-ip IP_ID]` + `detach-ip BASTION` (`POST
  /v1/bastions/{id}/{attach,detach}-ip` — attach-ip accepte un `public_ip_id`
  **optionnel** : fourni → réutilise cette IP du tenant ; omis → alloue auto).
  `--yes/-y` sur `vpc rm` + `detach-ip` (confirmations destructives). Anti-leak :
  sorties/help parlent de « passerelle VPN », « bastion », « VPC couverts »,
  « IP publique ». Tests : 6 nouveaux (vpn) + 7 (bastion). Aucun nouvel endpoint
  backend. Calqué sur `cetic ip attach` + `cetic vpc vnet add/rm`.
- `v0.29.0` — feat : options `--cloud-init`, `--bastion-access`, `--template-source`
  à la création compute (issues cetic-cloud-platform#343, cetic-cloud-cli#19).
  Sur `container create`, `vm create`, `vm-scale-set create`, `ct-scale-set create` :
  `--cloud-init PATH` lit un **fichier** cloud-config et l'envoie en `user_data`
  (Typer valide l'existence du fichier ; contenu validé côté backend), et
  `--bastion-access` (flag) envoie `bastion_access=true` (« Autoriser l'accès via
  le Bastion SSH », opt-in #307). `--template-source` (flag, **vm + container
  seulement** — sans objet pour un scale-set) envoie `is_template_source=true` →
  crée une **instance de préparation de template** (visible dans « Mes templates »,
  cf. console). Helper partagé `commands/_compute.py`
  (`read_cloud_init`/`apply_compute_access_options`) : les champs ne sont ajoutés
  au body **que** s'ils sont fournis (absence → défauts backend, pas de
  `user_data=""`). Aucun nouvel endpoint : le backend acceptait déjà ces trois
  champs sur les schémas de création.
- `v0.28.0` — feat : VPN site-à-site + message d'utilisation client WireGuard
  (issue cetic-cloud-platform#306). `cetic vpn peer add GATEWAY NAME --site
  CIDR[,CIDR...]` (répétable ou séparé par virgule) → envoie `peer_type="site"` +
  `site_cidrs=[...]` dans le POST `/v1/vpn/gateways/{id}/peers`. La logique
  Model A/B reste inchangée (`--managed` = la plateforme génère ; sinon keygen
  local). Sans `--site` → `peer_type="client"` (comportement inchangé). **Message
  d'utilisation** affiché après écriture du `.conf` par `peer add`, `config` et
  `rotate`, selon le champ `peer_type` de la réponse : pour un peer **client**,
  importer le fichier dans l'application **WireGuard** officielle
  (https://www.wireguard.com/install/) pour se connecter au VPN privé ; pour un
  peer **site**, déployer le fichier sur le routeur/pare-feu distant compatible
  WireGuard, activer l'IP forwarding et router le LAN/VNet à travers le tunnel.
  C'est le seul endroit où WireGuard est nommé côté client (format de l'artefact
  + nom de l'app — intentionnel). Help/sorties générales restent « VPN » / « accès
  privé » (anti-leak inchangé).
- `v0.27.0` — feat : surface CLI du VPN « accès privé » (issue cetic-cloud-platform#306).
  Sous-app `cetic vpn` : `gateway create/list/get/delete` (`--name/--region/--vpc`
  répétable 1-5/`--plan`/`--public-ip`/`--dns`/`--pool-cidr`/`--tags`), `peer add
  GATEWAY NAME [--managed] [--no-store] [--one-time]` (écrit `<NAME>.conf` en 0600),
  `peer list/rm`, `config GATEWAY PEER_ID` (re-download Model B uniquement, surface
  409/410), `rotate GATEWAY PEER_ID [--managed]`, `policy get/set` (set lit un
  fichier JSON `--file` ou stdin). **Deux modèles de clé** : souverain (défaut) =
  paire générée localement via `cryptography` X25519 (clamping standard), seule la
  pubkey envoyée, placeholder `__INJECT_LOCAL_PRIVATE_KEY__` substitué localement
  dans le `.conf` ; géré (`--managed`) = la plateforme renvoie le `.conf` complet.
  Nouvelle dépendance `cryptography>=42`. Endpoints backend : `GET/POST
  /v1/vpn/gateways`, `GET/DELETE /v1/vpn/gateways/{id}`, `POST/GET
  /v1/vpn/gateways/{id}/peers`, `GET /v1/vpn/gateways/{id}/peers/{pid}/config`,
  `POST .../rotate`, `DELETE .../peers/{pid}`, `GET/PUT /v1/vpn/gateways/{id}/policy`.
  Anti-leak : sorties/help parlent de « VPN » / « accès privé », jamais de
  WireGuard/LXC/FRR/nftables (le `.conf` reste un fichier WireGuard, c'est l'artefact
  attendu par le client `wg`).
- `v0.26.0` — feat : surface CLI du Bastion SSH (issue cetic-cloud-platform#307).
- `v0.25.0` — feat : surface CLI du Bastion SSH (issue cetic-cloud-platform#307).
  Sous-app `cetic bastion` (`list`/`get`/`create --name/--region/--vpc`/`delete`/
  `ca --kind user|host`/`revoke --serial/--key-id/--reason`/`krl`) + commande de
  premier niveau `cetic ssh <TARGET> [--login/--bastion/--ttl]` (auto-flow :
  paire ed25519 éphémère via `ssh-keygen` → `POST /v1/ssh/sign` → cert temporaire
  → `ssh` à travers le bastion `host=<TARGET>`, tmpdir nettoyé). Endpoints backend :
  `GET/POST /v1/bastions`, `GET/DELETE /v1/bastions/{id}`, `GET /v1/ssh/ca/{kind}/public`,
  `POST /v1/ssh/revoke`, `GET /v1/ssh/krl`, `POST /v1/ssh/sign`. Anti-leak : sorties
  parlent de « bastion » / « accès SSH sécurisé », jamais de LXC/Proxmox.
- `v0.24.0` — feat : `cetic lb/appgw` algorithme random + provider DNS IONOS (PR #20).
- `v0.23.0` — feat : `cetic lb plans` (kind=lb) + `cetic appgw plans` (kind=appgw)
  — complète le catalogue compute v0.22.0 (LB + AppGw manquaient). Anti-leak :
  help `container` « Containers (LXC) » → « (CT) », templates « LXC »/« QEMU » →
  « système (CT) »/« système (VM) », help `k8s --tier` reformulé (plus de « LXC
  proxy / HA actif/passif » → « frontal unique / frontal redondant (bascule
  auto) »). Endpoint inchangé : `GET /v1/compute/plans?kind=lb|appgw`.
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
