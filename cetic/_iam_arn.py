"""IAM ARN parser/builder/matcher — copie du backend.

Source de vérité : `apps/api/app/services/iam_arn.py` (cetic-cloud-platform).
Ce module est dupliqué côté CLI pour valider les `--resource ARN` AVANT
l'appel API (UX, fail-fast côté client). Toute modification de la
grammaire ARN doit être propagée des deux côtés.

Grammaire :
    arn:ccp:<service>:<region>:<tenant_id>:<resource_path>

Cf. `apps/docs-internal/content/services/iam-arn-scheme.md` pour la BNF
+ exemples + différences avec ARN AWS.
"""
from __future__ import annotations

import fnmatch
import re
import uuid
from dataclasses import dataclass
from typing import Iterable


# Whitelist services (cf. iam_catalog.SERVICES)
_KNOWN_SERVICES = frozenset({
    "iam", "registry", "bucket", "k8s", "vm", "container", "vpc", "lb",
    "publicip", "volume", "dbaas", "billing", "support", "org",
})

# Whitelist regions (lowercase) + vide
_KNOWN_REGIONS = frozenset({"rnn", "par", "abj", ""})

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ArnParts:
    """Décomposition d'un ARN."""

    service: str
    region: str
    tenant_id: str
    resource_path: str

    @property
    def is_wildcard_tenant(self) -> bool:
        return self.tenant_id == "*"

    @property
    def is_built_in(self) -> bool:
        return self.tenant_id == ""


def build_arn(
    service: str,
    region: str,
    tenant_id: str | uuid.UUID,
    resource_path: str,
) -> str:
    region_norm = (region or "").strip().lower()
    tenant_str = str(tenant_id) if isinstance(tenant_id, uuid.UUID) else tenant_id

    for label, val in (("service", service), ("region", region_norm),
                       ("tenant_id", tenant_str), ("resource_path", resource_path)):
        if ":" in val:
            raise ValueError(
                f"build_arn: segment `{label}` ne peut pas contenir `:` (reçu: {val!r})"
            )

    return f"arn:ccp:{service}:{region_norm}:{tenant_str}:{resource_path}"


def parse_arn(arn: str) -> ArnParts:
    """Parse un ARN strict — lève `ValueError` si malformé."""
    if not isinstance(arn, str):
        raise ValueError(f"parse_arn: doit être une chaîne (reçu type {type(arn).__name__})")
    if not arn:
        raise ValueError("parse_arn: chaîne vide")

    parts = arn.split(":", 5)
    if len(parts) < 6:
        raise ValueError(
            f"parse_arn: format invalide — attendu 6 segments séparés par `:` (reçu: {arn!r})"
        )

    prefix, ns, service, region, tenant_id, resource_path = parts

    if prefix != "arn":
        raise ValueError(f"parse_arn: doit commencer par `arn:` (reçu prefix={prefix!r})")
    if ns != "ccp":
        raise ValueError(f"parse_arn: namespace doit être `ccp` (reçu: {ns!r})")
    if not service:
        raise ValueError("parse_arn: `service` ne peut pas être vide")
    if not resource_path:
        raise ValueError("parse_arn: `resource_path` ne peut pas être vide")

    if not _is_segment_valid_charset(service):
        raise ValueError(
            f"parse_arn: `service` contient des caractères invalides (reçu: {service!r})"
        )
    if "*" not in service and "?" not in service:
        if service not in _KNOWN_SERVICES:
            raise ValueError(
                f"parse_arn: `service` inconnu — reçu {service!r}, "
                f"attendu un de {sorted(_KNOWN_SERVICES)} ou wildcard."
            )

    if not _is_segment_valid_charset(region):
        raise ValueError(
            f"parse_arn: `region` contient des caractères invalides (reçu: {region!r})"
        )
    region_norm = region.lower()
    if "*" not in region_norm and "?" not in region_norm:
        if region_norm not in _KNOWN_REGIONS:
            raise ValueError(
                f"parse_arn: `region` inconnue — reçu {region!r}, "
                f"attendu un de {sorted((_KNOWN_REGIONS - {''}) | {'<vide>'})} ou wildcard."
            )

    if not _is_segment_valid_charset(tenant_id):
        raise ValueError(
            f"parse_arn: `tenant_id` contient des caractères invalides (reçu: {tenant_id!r})"
        )
    if tenant_id and "*" not in tenant_id and "?" not in tenant_id:
        if not _UUID_RE.match(tenant_id):
            raise ValueError(
                f"parse_arn: `tenant_id` doit être un UUID v4, vide, ou wildcard (reçu: {tenant_id!r})"
            )

    if not _is_segment_valid_charset(resource_path):
        raise ValueError(
            f"parse_arn: `resource_path` contient des caractères invalides (reçu: {resource_path!r})"
        )

    return ArnParts(
        service=service,
        region=region_norm,
        tenant_id=tenant_id,
        resource_path=resource_path,
    )


def _is_segment_valid_charset(segment: str) -> bool:
    if not segment:
        return True
    return all(c.isalnum() or c in "-_.*?/" for c in segment)


def match_arn(pattern: str, target: str) -> bool:
    if not isinstance(pattern, str) or not isinstance(target, str):
        return False

    if pattern == "*":
        return True

    target_parts = target.split(":", 5)
    if len(target_parts) < 6:
        return False
    if target_parts[0] != "arn" or target_parts[1] != "ccp":
        return False

    pattern_parts = pattern.split(":", 5)
    if len(pattern_parts) < 6:
        return False
    if pattern_parts[0] != "arn" or pattern_parts[1] != "ccp":
        return False

    for idx in (2, 3, 4):
        if not fnmatch.fnmatchcase(target_parts[idx], pattern_parts[idx]):
            return False

    return fnmatch.fnmatchcase(target_parts[5], pattern_parts[5])


def matches_any(patterns: Iterable[str], target: str) -> bool:
    for p in patterns:
        if match_arn(p, target):
            return True
    return False


def validate_arn_or_pattern(arn: str) -> None:
    """Validation CLI : accepte `*` (wildcard global) OU un ARN parsable.

    Lève `ValueError` avec message UX si invalide. Utilisé par les
    commandes `cetic iam simulate` (--resource) et `cetic iam roles
    create` pour valider les ARN dans le policy_document avant POST.
    """
    if arn == "*":
        return
    parse_arn(arn)
