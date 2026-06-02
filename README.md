# CETIC Cloud CLI

Official command-line interface for [CETIC Cloud Platform](https://docs.cloud.cetic-group.com).

[![Release](https://img.shields.io/github/v/release/cetic-group/cetic-cloud-cli)](https://github.com/cetic-group/cetic-cloud-cli/releases)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](LICENSE)

```bash
cetic auth login
cetic vm create --name web-01 --region RNN --plan small
cetic k8s kubeconfig <cluster-id> > ~/.kube/config
cetic db pg create --name app-db --plan dev
```

## Installation

### Pre-built binary (recommended)

Download for your platform from the [Releases](https://github.com/cetic-group/cetic-cloud-cli/releases) page.

```bash
# Linux x86_64
curl -L https://github.com/cetic-group/cetic-cloud-cli/releases/latest/download/cetic-linux-amd64 -o cetic
chmod +x cetic && sudo mv cetic /usr/local/bin/

# Linux ARM64
curl -L https://github.com/cetic-group/cetic-cloud-cli/releases/latest/download/cetic-linux-arm64 -o cetic
chmod +x cetic && sudo mv cetic /usr/local/bin/

# macOS Apple Silicon
curl -L https://github.com/cetic-group/cetic-cloud-cli/releases/latest/download/cetic-darwin-arm64 -o cetic
chmod +x cetic && sudo mv cetic /usr/local/bin/

# macOS Intel
curl -L https://github.com/cetic-group/cetic-cloud-cli/releases/latest/download/cetic-darwin-amd64 -o cetic
chmod +x cetic && sudo mv cetic /usr/local/bin/

# Windows x86_64 (PowerShell)
Invoke-WebRequest -Uri https://github.com/cetic-group/cetic-cloud-cli/releases/latest/download/cetic-windows-amd64.exe -OutFile cetic.exe
```

> **macOS Gatekeeper** : `xattr -d com.apple.quarantine cetic` si bloqué.

### From source (Python 3.12+)

```bash
pip install git+https://github.com/cetic-group/cetic-cloud-cli.git
```

## Configuration

The CLI reads configuration from environment variables (priority over config file `~/.config/cetic/config.toml`) :

| Variable | Description | Default |
|---|---|---|
| `CCP_API_KEY` | API key (Bearer token) | — *(required)* |
| `CCP_REGION` | Active region | `RNN` |
| `CCP_OUTPUT` | Output format | `table` *(table / json / yaml)* |
| `CCP_LANG` | Language | `fr` *(fr / en)* |

Generate an API key in the CETIC Cloud console under **Settings → API Keys**, or use `cetic auth login` for interactive authentication.

> **Trousseau système** : les mots de passe admin des registries de conteneurs (`cetic registry`) sont stockés dans le trousseau système via la lib `keyring` (Keychain macOS, libsecret/GNOME Keyring Linux, Credential Manager Windows). Le CLI propose la sauvegarde à la création — vous pouvez aussi répondre `n` et fournir le mot de passe au login interactif.

### Keyring SA token (depuis v0.8.0)

Les **tokens de service account** (`ccp_sa_*`) sont également stockables dans le
trousseau système. À la création / rotation, le CLI affiche le token UNE SEULE
FOIS et propose la sauvegarde :

```bash
cetic service-account create --name ci-pipeline --expires-in-days 365 \
  --save-keyring                                  # stocke sans prompt
# ou sans le flag : prompt interactif Y/n
cetic service-account create --name ci-pipeline --expires-in-days 365
```

À la rotation, le token est remplacé dans le trousseau :

```bash
cetic service-account rotate ci-pipeline --save-keyring
```

À la révocation, l'entrée du trousseau est supprimée automatiquement
(`cetic service-account revoke <id>`). Les service names utilisés :

| Service trousseau | Famille |
|---|---|
| `cetic-registry` | Mots de passe admin de registry |
| `cetic-service-account` | Tokens `ccp_sa_*` |

## Application Gateways (depuis v0.11.0)

Service L7 distinct du Load Balancer L4 (`cetic lb`). L'AppGW route le trafic
HTTP/HTTPS par hostname et path, gère les certificats Let's Encrypt
automatiquement (SNI multi-domaine), et applique des politiques L7 (rate
limit, IP allow/deny, WAF, CORS, basic auth) — tout en un seul produit.

1. **Créer une gateway** (provisionne en ~3-5 min) :

   ```bash
   cetic appgw create --name web-edge --region RNN --plan small \
     --vpc prod --vnet web-tier
   ```

2. **Ajouter un listener** (hostname + cert ACME automatique) :

   ```bash
   # Domaine pointant déjà vers la gateway (challenge HTTP-01)
   cetic appgw listener add web-edge \
     --hostname web-edge-abc.app.cloud.cetic-group.com --acme-challenge http01

   # Domaine custom validé par DNS (challenge DNS-01 + credentials provider)
   cetic appgw listener add web-edge --hostname api.example.com \
     --acme-challenge dns01 --acme-dns-provider cloudflare \
     --acme-dns-credential api_token=xxx
   ```

3. **Créer un target group + ajouter des backends** (containers, VMs, ou IPs) :

   ```bash
   cetic appgw tg create web-edge --name api-pool --algorithm leastconn
   cetic appgw tg member add web-edge --tg-id <tg-uuid> \
     --container <container-uuid> --port 8080
   cetic appgw tg member add web-edge --tg-id <tg-uuid> \
     --vm <vm-uuid> --port 3000 --weight 200
   ```

4. **Créer une route** (host implicite via listener + path + policies) :

   ```bash
   # Route simple : tout vers un target group
   cetic appgw route create web-edge \
     --listener-id <listener-uuid> --target-group-id <tg-uuid>

   # Route path-based avec rate limit + WAF
   cetic appgw route create web-edge \
     --listener-id <listener-uuid> --target-group-id <api-tg-uuid> \
     --path /api --priority 50 --rate-limit 100 --waf-preset strict

   # Route avec IP allowlist (admin endpoint)
   cetic appgw route create web-edge \
     --listener-id <listener-uuid> --target-group-id <admin-tg-uuid> \
     --path /admin --allow-cidr 10.0.0.0/8 --allow-cidr 192.168.1.0/24
   ```

5. **Vérifier la santé des backends** (couleurs : vert UP, rouge DOWN) :

   ```bash
   cetic appgw health web-edge
   ```

Plans disponibles : `small` (50 routes, 100 req/s), `medium` (200 routes, 1000
req/s), `large` (1000 routes, 10000 req/s + GeoIP).

> AppGW vs LB : utilisez `cetic appgw` pour HTTP/HTTPS avec routage host/path
> et politiques L7 ; gardez `cetic lb` pour TCP/UDP brut (Postgres, gRPC, jeux).

## IAM quickstart (depuis v0.8.0)

CETIC Cloud expose un système IAM AWS-style en additif du RBAC owner/admin/member/viewer.
Cf. [docs/iam](https://docs.cloud.cetic-group.com/services/iam) pour le détail du modèle.

1. **Examiner les 10 rôles built-in** (catalogue CETIC, non éditables) :

   ```bash
   cetic iam built-ins list                       # AdminAll, RegistryAdmin, BucketReader, ...
   cetic iam roles get RegistryAdmin --reveal-policy
   ```

2. **Créer un rôle custom** depuis un fichier JSON :

   ```json
   // ci-deployer.policy.json
   {
     "statements": [
       {
         "sid": "AllowRegistryPushOnMyReg",
         "effect": "Allow",
         "actions": ["registry:Pull", "registry:Push"],
         "resources": ["arn:ccp:registry:rnn:00000000-0000-0000-0000-000000000000:registry/myreg/*"]
       }
     ]
   }
   ```

   ```bash
   cetic iam roles create --name CIDeployer \
     --policy-file ./ci-deployer.policy.json \
     --description "Push CI vers myreg"
   ```

   Les ARN dans `resources` sont validés côté CLI **avant** l'appel API
   (parse strict identique au backend — `apps/api/app/services/iam_arn.py`).

3. **Attacher le rôle à un service account ou un membre** :

   ```bash
   cetic iam roles attach CIDeployer \
     --principal-type service_account --principal-id ci-pipeline \
     --expires-at 2027-01-01T00:00:00Z
   ```

   `--principal-id` accepte UUID OU nom (api_key/service_account) OU email
   (org_member). `ccks_workload` exige un UUID.

4. **Simuler une décision** avant déploiement :

   ```bash
   cetic iam simulate \
     --action registry:Pull \
     --resource "arn:ccp:registry:rnn:UUID:registry/myreg" \
     --principal-type service_account --principal-id ci-pipeline
   ```

   Sortie colorée : vert (Allow), rouge (ExplicitDeny), gris (ImplicitDeny).

5. **Voir mes permissions effectives** :

   ```bash
   cetic iam who-am-i --effective-permissions
   ```

## Quickstart

```bash
# Authentication
cetic auth login                  # interactive (email + password)
cetic auth whoami                 # show current identity

# Compute
cetic vm list
cetic vm create --name web-01 --region RNN --plan small
cetic container list
cetic container create --name api --plan small --template ubuntu-24.04

# Kubernetes
cetic k8s list
cetic k8s create --name prod --region RNN --pool-plan medium --pool-min 1 --pool-max 5
cetic k8s kubeconfig <cluster-id> > ~/.kube/config

# Storage
cetic volume list
cetic bucket list
cetic bucket create --name backups --region RNN

# Networking
cetic vpc list
cetic vpc create --name prod --region RNN --cidr 10.10.0.0/16
cetic ip list
cetic ip allocate --region RNN --label passerelle-prod --description "IP fixe de prod"
cetic ip allocate --region RNN --quantity 3 --label ip-fixe-api   # 3 IPs d'un coup
cetic ip update <ip-uuid> --label nouveau-nom

# Load Balancers L4 avec certificat Let's Encrypt (depuis v0.19.0)
cetic lb create --name web-lb --region RNN --vnet <vnet-uuid> \
  --listener-protocol https --listener-port 443 \
  --domain www.example.com --acme-challenge http01 \
  --backend container:<ct-uuid>:8080
cetic lb acme-providers                                      # catalogue DNS-01
cetic lb backend add <lb-uuid> <listener-uuid> --container <ct-uuid> --port 8080

# Application Gateways (L7 HTTP/HTTPS routing, depuis v0.11.0)
cetic appgw create --name web-edge --region RNN --plan small --vpc prod --vnet web-tier
cetic appgw listener add web-edge --hostname api.example.com --acme-challenge http01
cetic appgw tg create web-edge --name api-pool
cetic appgw tg member add web-edge --tg-id <tg-uuid> --container <ct-uuid> --port 8080
cetic appgw route create web-edge --listener-id <lst-uuid> --target-group-id <tg-uuid> \
  --path /api --rate-limit 100 --waf-preset strict
cetic appgw health web-edge                                  # UP/DOWN par backend

# Databases
cetic db pg create --name app-db --plan dev
cetic db pg credentials <id>

# IAM Roles v1 (AWS-style, depuis v0.8.0)
cetic iam roles list                                     # custom + built-ins
cetic iam built-ins list                                 # 10 rôles built-in CETIC
cetic iam roles create --name CIDeployer --policy-file ./ci-deployer.policy.json
cetic iam roles attach CIDeployer --principal-type service_account --principal-id ci-pipeline
cetic iam who-am-i --effective-permissions
cetic iam simulate --action registry:Pull --resource "arn:ccp:registry:rnn:UUID:registry/myreg"

# Service accounts (token ccp_sa_, distinct des API keys)
cetic service-account create --name ci-pipeline --expires-in-days 365 --save-keyring
cetic service-account rotate ci-pipeline --save-keyring   # nouveau token, ancien invalidé
cetic service-account revoke ci-pipeline --yes

# Container registry (CCR)
cetic registry create -n myreg --region RNN              # default: --no-public --private
cetic registry create -n public-reg --region RNN --public --no-private
cetic registry update myreg --public                     # toggle Internet exposure on
cetic registry update myreg --tags env=prod,team=core    # edit tags
cetic registry login myreg                               # docker login via subprocess + trousseau
cetic registry user add myreg --username ci
cetic registry acl set myreg --user ci --repo "myapp/*" --actions pull,push
cetic registry repos myreg --all
cetic registry tags myreg myapp/api
cetic registry tag delete myreg myapp/api v1.0.0 --yes
cetic registry gc myreg --wait

# Templates (custom)
cetic template list
cetic template get <id>

# Help
cetic --help
cetic <command> --help
```

## Development

```bash
# Setup
git clone https://github.com/cetic-group/cetic-cloud-cli.git
cd cetic-cloud-cli
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run from source
cetic --help

# Tests
pytest

# Lint
ruff check .

# Build standalone binary
pyinstaller --clean --noconfirm cetic.spec
./dist/cetic --help
```

## Releases

Releases are published automatically on tag push (`v*`) via GitHub Actions :
- Builds PyInstaller binaries for Linux (amd64/arm64), macOS (Intel/Apple Silicon), Windows
- Uploads to GitHub Releases with SHA256 checksums

To publish a new version :
```bash
# Bump version in pyproject.toml
git tag v0.5.4 && git push origin v0.5.4
```

## License

[Apache 2.0](LICENSE)

## Links

- [Documentation](https://docs.cloud.cetic-group.com)
- [Console](https://console.cloud.cetic-group.com)
- [Terraform Provider](https://github.com/cetic-group/terraform-provider-cetic-cloud-platform)
- [Issues](https://github.com/cetic-group/cetic-cloud-cli/issues)
