"""Messages d'aide à la connexion, partagés par toutes les commandes.

Le login par e-mail et mot de passe n'existe plus dans la CLI
(cetic-cloud-platform#2004) : les messages ne doivent plus renvoyer vers
`cetic auth login` sans option, qui échoue toujours.
"""
from __future__ import annotations

CONNEXION = (
    "`cetic auth login --sso github|google`, `cetic auth login --api-key` "
    "ou la variable `CCP_API_KEY`"
)

NON_AUTHENTIFIE = f"Non authentifié — connectez-vous avec {CONNEXION}."
