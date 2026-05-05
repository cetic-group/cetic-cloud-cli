# CETIC Cloud CLI

Binary releases of the `cetic` CLI for CETIC Cloud Platform.

## Installation

Download the binary for your platform from the [Releases](https://github.com/cetic-group/cetic-cloud-cli/releases) page.

```bash
# Linux x86_64
curl -L https://github.com/cetic-group/cetic-cloud-cli/releases/latest/download/cetic-linux-amd64 -o cetic
chmod +x cetic
sudo mv cetic /usr/local/bin/

# macOS Apple Silicon
curl -L https://github.com/cetic-group/cetic-cloud-cli/releases/latest/download/cetic-darwin-arm64 -o cetic
chmod +x cetic
sudo mv cetic /usr/local/bin/

# Windows x86_64 (PowerShell)
Invoke-WebRequest -Uri https://github.com/cetic-group/cetic-cloud-cli/releases/latest/download/cetic-windows-amd64.exe -OutFile cetic.exe
# Move cetic.exe to a directory in your PATH
```

> **macOS** : si Gatekeeper bloque le binaire, exécute : `xattr -d com.apple.quarantine cetic`

## Configuration

The CLI reads configuration from environment variables:

```bash
export CCP_API_KEY="ccp_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export CCP_API_URL="https://api.cloud.cetic-group.com"   # optional — default endpoint
export CCP_REGION="RNN"                                   # optional — default region (RNN, PAR, ABJ)
export CCP_OUTPUT="table"                                 # optional — table | json | yaml
```

`CCP_API_KEY` is required for all commands. Generate an API key in the CETIC Cloud console under **Settings → API Keys**.

## Usage

```bash
cetic --help
cetic auth login              # interactive login (alternative to API key)
cetic auth whoami             # show current identity

cetic vm list                 # list VM instances
cetic container list          # list containers
cetic k8s list                # list Kubernetes clusters
cetic k8s kubeconfig <id>     # download kubeconfig for a cluster

cetic db pg list              # list PostgreSQL instances
cetic db valkey list          # list Valkey instances

cetic vpc list                # list VPCs
cetic lb list                 # list load balancers
cetic ip list                 # list public IPs

cetic billing credits         # show free credit balance
cetic billing usage           # show current usage
```

## Documentation

[docs.cloud.cetic-group.com](https://docs.cloud.cetic-group.com)
