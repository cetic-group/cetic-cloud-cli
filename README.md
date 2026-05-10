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

# macOS Apple Silicon
curl -L https://github.com/cetic-group/cetic-cloud-cli/releases/latest/download/cetic-darwin-arm64 -o cetic
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
| `CCP_API_URL` | API endpoint | `https://api.cloud.cetic-group.com` |
| `CCP_REGION` | Active region | `RNN` |
| `CCP_OUTPUT` | Output format | `table` *(table / json / yaml)* |
| `CCP_LANG` | Language | `fr` *(fr / en)* |

Generate an API key in the CETIC Cloud console under **Settings → API Keys**, or use `cetic auth login` for interactive authentication.

> **Trousseau système** : les mots de passe admin des registries de conteneurs (`cetic registry`) sont stockés dans le trousseau système via la lib `keyring` (Keychain macOS, libsecret/GNOME Keyring Linux, Credential Manager Windows). Le CLI propose la sauvegarde à la création — vous pouvez aussi répondre `n` et fournir le mot de passe au login interactif.

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
cetic ip allocate --region RNN

# Databases
cetic db pg create --name app-db --plan dev
cetic db pg credentials <id>

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
