"""Commande d'administration TechNova : ``stockmanager``.

La CLI ne contient aucune logique métier : elle délègue aux services de la plateforme.
"""

import argparse
import getpass
import os
import sys
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import create_db_engine, create_session_factory
from app.core.errors import AppError
from app.platform.catalog.loader import CatalogError, load_catalog
from app.platform.catalog.sync import sync_catalog
from app.platform.provisioning.service import ProvisionTenantCommand, TenantProvisioningService
from app.platform.registry import get_registry
from app.platform.subscriptions.models import BillingPeriod
from app.platform.tenancy.models import SiteKind
from app.shared.clock import utcnow


def _session(url: str, settings: Settings) -> Session:
    return create_session_factory(create_db_engine(url, settings))()


def cmd_catalog_sync(args: argparse.Namespace, settings: Settings) -> int:
    catalog = load_catalog(get_registry())
    # Le catalogue est en lecture seule pour le rôle applicatif : rôle propriétaire requis.
    with _session(settings.migration_database_url, settings) as session:
        report = sync_catalog(session, catalog)
        session.commit()
    print(
        f"Catalogue synchronisé : {report.profiles} profils, {report.plans} plans, "
        f"{report.policies} politiques d'abonnement."
    )
    for code in report.deactivated_profiles:
        print(f"  profil désactivé : {code}")
    for code in report.deactivated_plans:
        print(f"  plan désactivé : {code}")
    return 0


def cmd_catalog_check(args: argparse.Namespace, settings: Settings) -> int:
    catalog = load_catalog(get_registry())
    print("Catalogue valide.")
    print("Profils :", ", ".join(sorted(catalog.profiles)))
    print("Plans    :", ", ".join(sorted(catalog.plans)))
    return 0


def _read_password(args: argparse.Namespace) -> str | None:
    if args.owner_password_stdin:
        return sys.stdin.readline().rstrip("\n") or None
    env_password = os.environ.get("SM_OWNER_PASSWORD")
    if env_password:
        return env_password
    if not sys.stdin.isatty():
        return None
    first = getpass.getpass("Mot de passe provisoire du propriétaire (vide si compte existant) : ")
    if not first:
        return None
    if getpass.getpass("Confirmation : ") != first:
        raise AppError("Les mots de passe ne correspondent pas", code="password_mismatch")
    return first


def cmd_create_tenant(args: argparse.Namespace, settings: Settings) -> int:
    registry = get_registry()
    catalog = load_catalog(registry)
    command = ProvisionTenantCommand(
        name=args.name,
        slug=args.slug,
        profile_code=args.profile,
        plan_code=args.plan,
        billing_period=BillingPeriod(args.billing),
        owner_email=args.owner_email,
        owner_full_name=args.owner_name,
        owner_password=_read_password(args),
        trial_days=args.trial_days,
        first_site_name=args.site_name,
        first_site_code=args.site_code,
        first_site_kind=SiteKind(args.site_kind),
        currency=args.currency,
    )
    with _session(settings.database_url, settings) as session:
        result = TenantProvisioningService(
            session, settings, registry, catalog.role_templates, utcnow()
        ).provision(command, actor="cli")
        session.commit()
    print(f"Tenant créé : {result.tenant_id}")
    print(f"  site initial     : {result.site_id}")
    owner_state = "créé" if result.owner_created else "existant"
    print(f"  propriétaire     : {result.owner_user_id} ({owner_state})")
    print(f"  modules activés  : {', '.join(result.enabled_modules) or '(aucun)'}")
    if result.owner_created:
        print("  Le propriétaire devra changer son mot de passe à la première connexion.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stockmanager", description="Administration TechNova")
    sub = parser.add_subparsers(dest="command", required=True)

    catalog = sub.add_parser("catalog", help="Catalogue : profils, plans, politiques")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_sub.add_parser(
        "sync", help="Synchroniser les fichiers du catalogue en base"
    ).set_defaults(func=cmd_catalog_sync)
    catalog_sub.add_parser("check", help="Valider les fichiers du catalogue").set_defaults(
        func=cmd_catalog_check
    )

    create = sub.add_parser("create-tenant", help="Provisionner une nouvelle entreprise")
    create.add_argument("--name", required=True, help="Raison sociale")
    create.add_argument("--slug", required=True, help="Identifiant court unique (ex. abc-ouaga)")
    create.add_argument("--profile", required=True, help="Profil d'activité (ex. alimentation)")
    create.add_argument("--plan", required=True, choices=["STANDARD", "ENTREPRISE"])
    create.add_argument("--billing", default="monthly", choices=[p.value for p in BillingPeriod])
    create.add_argument("--trial-days", type=int, default=None, help="Démarrer en essai N jours")
    create.add_argument("--owner-email", required=True)
    create.add_argument("--owner-name", required=True)
    create.add_argument(
        "--owner-password-stdin",
        action="store_true",
        help="Lire le mot de passe provisoire sur l'entrée standard",
    )
    create.add_argument("--site-name", default="Site principal")
    create.add_argument("--site-code", default="PRINCIPAL")
    create.add_argument("--site-kind", default="store", choices=[k.value for k in SiteKind])
    create.add_argument("--currency", default="XOF")
    create.set_defaults(func=cmd_create_tenant)
    return parser


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args, settings or get_settings())
        return code
    except (AppError, CatalogError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
