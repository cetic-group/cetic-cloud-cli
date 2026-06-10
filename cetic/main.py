"""cetic — CETIC Cloud Platform CLI.

Utilisation :
  cetic auth login
  cetic auth whoami
  cetic config view
  cetic config set region PAR
  cetic region list
  cetic key add --name "dev" --file ~/.ssh/id_ed25519.pub
  cetic key list
  cetic key delete <id>
  cetic registry create -n myreg -r RNN              # privé par défaut
  cetic registry create -n pub -r RNN --public --no-private
  cetic registry update myreg --public               # toggle expose à chaud
  cetic registry login myreg
  cetic registry repos myreg --all
  cetic registry user add myreg --username ci

Variables d'environnement :
  CCP_API_KEY    — clé API (Bearer token)
  CCP_REGION     — région active (RNN | PAR | ABJ)
  CCP_OUTPUT     — format (table | json | yaml)
  CCP_LANG       — langue (fr | en)
  CCP_API_URL    — surcharge URL API
"""

import typer
from rich import print as rprint

from cetic import __version__
from cetic.commands import (
    api_key,
    appgw,
    auth,
    bastion,
    billing,
    bucket,
    config_cmd,
    container,
    db,
    iam,
    ip,
    k8s,
    key,
    lb,
    member,
    org,
    quota,
    region,
    registry,
    scale_set,
    secret,
    service_account,
    ssh,
    support,
    tag,
    template,
    vm,
    volume,
    vpc,
)

app = typer.Typer(
    name="cetic",
    help="CETIC Cloud Platform CLI — Deep infrastructure. Endless possibilities.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.add_typer(auth.app, name="auth")
app.add_typer(config_cmd.app, name="config")
app.add_typer(region.app, name="region")
app.add_typer(key.app, name="key")
app.add_typer(container.app, name="container")
app.add_typer(vm.app, name="vm")
app.add_typer(vpc.app, name="vpc")
app.add_typer(volume.app, name="volume")
app.add_typer(bucket.app, name="bucket")
app.add_typer(lb.app, name="lb")
app.add_typer(appgw.app, name="appgw")
app.add_typer(ip.app, name="ip")
app.add_typer(iam.app, name="iam")
app.add_typer(db.app, name="db")
app.add_typer(k8s.app, name="k8s")
app.add_typer(billing.app, name="billing")
app.add_typer(scale_set.container_app, name="scale-set")
app.add_typer(scale_set.vm_app, name="vm-scale-set")
app.add_typer(secret.app, name="secret")
app.add_typer(service_account.app, name="service-account")
app.add_typer(template.app, name="template")
app.add_typer(api_key.app, name="api-key")
app.add_typer(member.app, name="member")
app.add_typer(support.app, name="support")
app.add_typer(org.app, name="org")
app.add_typer(quota.app, name="quota")
app.add_typer(registry.app, name="registry")
app.add_typer(tag.app, name="tag")
app.add_typer(bastion.app, name="bastion")

# Commande de premier niveau (PAS sous une sous-app) : ouvre une session SSH
# sécurisée vers une cible privée via le bastion.
app.command(name="ssh")(ssh.ssh)


def version_callback(value: bool) -> None:
    if value:
        rprint(f"cetic version [bold cyan]{__version__}[/bold cyan]")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(  # noqa: FBT001
        False,
        "--version",
        "-v",
        help="Affiche la version et quitte.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """CETIC Cloud Platform CLI"""


if __name__ == "__main__":
    app()
