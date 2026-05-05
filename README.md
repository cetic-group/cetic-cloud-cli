# CETIC Cloud CLI

Binary releases of the `cetic` CLI for CETIC Cloud Platform.

## Installation

Download the binary for your platform from the [Releases](https://github.com/cetic-group/cetic-cloud-cli/releases) page.

```bash
# Linux / macOS
curl -L https://github.com/cetic-group/cetic-cloud-cli/releases/latest/download/cetic-linux-amd64 -o cetic
chmod +x cetic
sudo mv cetic /usr/local/bin/

# macOS Apple Silicon
curl -L https://github.com/cetic-group/cetic-cloud-cli/releases/latest/download/cetic-darwin-arm64 -o cetic
chmod +x cetic
sudo mv cetic /usr/local/bin/
```

> **macOS** : si Gatekeeper bloque le binaire, exécute : `xattr -d com.apple.quarantine cetic`

## Usage

```bash
cetic --help
cetic auth login
cetic vm list
cetic k8s kubeconfig <cluster-id>
```

## Documentation

[docs.cloud.cetic-group.com](https://docs.cloud.cetic-group.com)
