"""Commande d'administration TechNova : ``stockmanager``.

La CLI ne contient aucune logique métier : elle délègue aux services de la plateforme.
"""

import argparse
import getpass
import os
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.console.admins import (
    create_platform_admin,
    list_platform_admins,
    revoke_platform_admin,
)
from app.console.audit import CLI_ACTOR, PlatformActor
from app.core.config import Settings, get_settings
from app.core.db import create_db_engine, create_session_factory, set_db_context
from app.core.errors import AppError, NotFoundError
from app.platform.catalog.loader import CatalogError, load_catalog
from app.platform.catalog.sync import sync_catalog
from app.platform.profiles.service import change_business_profile
from app.platform.provisioning.service import ProvisionTenantCommand, TenantProvisioningService
from app.platform.registry import get_registry
from app.platform.subscriptions.models import BillingPeriod
from app.platform.subscriptions.service import change_plan
from app.platform.tenancy.models import SiteKind, Tenant
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
        f"Catalogue synchronisé : {report.sectors} secteurs, {report.ux_profiles} profils UX, "
        f"{report.profiles} profils, {report.plans} plans, {report.countries} pays, "
        f"{report.policies} politiques d'abonnement."
    )
    for code in report.deactivated_sectors:
        print(f"  secteur désactivé : {code}")
    for code in report.deactivated_ux_profiles:
        print(f"  profil UX désactivé : {code}")
    for code in report.deactivated_profiles:
        print(f"  profil désactivé : {code}")
    for code in report.deactivated_plans:
        print(f"  plan désactivé : {code}")
    for code in report.deactivated_countries:
        print(f"  pays désactivé : {code}")
    return 0


def cmd_catalog_check(args: argparse.Namespace, settings: Settings) -> int:
    catalog = load_catalog(get_registry())
    print("Catalogue valide.")
    for sector in sorted(catalog.sectors.values(), key=lambda s: s.sort_order):
        profiles = sorted(
            (p for p in catalog.profiles.values() if p.sector == sector.code),
            key=lambda p: p.sort_order,
        )
        print(f"{sector.code:<13}: {', '.join(p.code for p in profiles)}")
    print("Profils UX   :", ", ".join(sorted(catalog.ux_profiles)))
    print("Plans        :", ", ".join(sorted(catalog.plans)))
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
        country_code=args.country,
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


def cmd_change_plan(args: argparse.Namespace, settings: Settings) -> int:
    with _session(settings.database_url, settings) as session:
        previous, plan = change_plan(session, args.tenant_id, args.plan, actor="cli")
        session.commit()
    if previous == plan:
        print(f"Plan inchangé : {plan}")
    else:
        print(f"Plan modifié : {previous} → {plan} (données conservées)")
    return 0


def cmd_change_profile(args: argparse.Namespace, settings: Settings) -> int:
    with _session(settings.database_url, settings) as session:
        set_db_context(session, tenant_id=args.tenant_id, user_id=None)
        tenant = session.get(Tenant, args.tenant_id)
        if tenant is None:
            raise NotFoundError("Tenant introuvable", code="tenant_not_found")
        change = change_business_profile(session, get_registry(), tenant, args.profile, actor="cli")
        session.commit()
    if not change.changed:
        print(f"Profil inchangé : {change.profile}")
    else:
        print(f"Profil modifié : {change.previous} → {change.profile} (données conservées)")
        if change.enabled_modules:
            print(f"  modules activés : {', '.join(change.enabled_modules)}")
    return 0


def _cli_actor() -> PlatformActor:
    """Auteur des actions de la CLI dans le journal de la plateforme (compte système)."""
    try:
        return PlatformActor(user_id=None, label=f"cli:{getpass.getuser()}")
    except (KeyError, OSError):
        return CLI_ACTOR


def _read_admin_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = os.environ.get("SM_PLATFORM_ADMIN_PASSWORD", "")
        if not password and sys.stdin.isatty():
            password = getpass.getpass("Mot de passe de l'administrateur TechNova : ")
            if getpass.getpass("Confirmation : ") != password:
                raise AppError("Les mots de passe ne correspondent pas", code="password_mismatch")
    if not password:
        raise AppError("Mot de passe requis", code="password_required")
    return password


def cmd_platform_admin_create(args: argparse.Namespace, settings: Settings) -> int:
    password = _read_admin_password(args)
    # Rôle propriétaire : ni le rôle applicatif ni celui de la console ne peuvent attribuer
    # le statut d'administrateur TechNova.
    with _session(settings.migration_database_url, settings) as session:
        user = create_platform_admin(
            session,
            settings,
            email=args.email,
            full_name=args.name,
            password=password,
            actor=_cli_actor(),
        )
        session.commit()
    print(f"Administrateur TechNova créé : {user.email} ({user.id})")
    print("  Accès : console TechNova uniquement (aucune entreprise).")
    return 0


def cmd_platform_admin_revoke(args: argparse.Namespace, settings: Settings) -> int:
    with _session(settings.migration_database_url, settings) as session:
        user = revoke_platform_admin(session, email=args.email, actor=_cli_actor())
        session.commit()
    print(f"Statut retiré et compte fermé : {user.email} (sessions de la console révoquées)")
    return 0


def cmd_platform_admin_list(args: argparse.Namespace, settings: Settings) -> int:
    with _session(settings.migration_database_url, settings) as session:
        admins = list_platform_admins(session)
    if not admins:
        print("Aucun administrateur TechNova.")
    for user in admins:
        state = "actif" if user.is_active else "inactif"
        print(f"{user.email:<40} {user.full_name} ({state})")
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
    create.add_argument(
        "--profile",
        "--business-profile",
        dest="profile",
        required=True,
        help="Profil d'activité <secteur>.<activité> (ex. retail.alimentation)",
    )
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
    create.add_argument(
        "--country", required=True, help="Pays ISO 3166-1 alpha-2 (ex. BF), obligatoire"
    )
    create.add_argument("--currency", default=None, help="Défaut : devise du pays")
    create.set_defaults(func=cmd_create_tenant)

    change = sub.add_parser("change-plan", help="Changer le plan d'une entreprise")
    change.add_argument("--tenant-id", required=True, type=uuid.UUID)
    change.add_argument("--plan", required=True, choices=["STANDARD", "ENTREPRISE"])
    change.set_defaults(func=cmd_change_plan)

    profile = sub.add_parser(
        "change-profile", help="Changer le profil d'activité d'une entreprise (données conservées)"
    )
    profile.add_argument("--tenant-id", required=True, type=uuid.UUID)
    profile.add_argument(
        "--profile",
        "--business-profile",
        dest="profile",
        required=True,
        help="ex. restaurant.maquis",
    )
    profile.set_defaults(func=cmd_change_profile)

    admins = sub.add_parser(
        "platform-admin", help="Administrateurs TechNova (console d'administration)"
    )
    admins_sub = admins.add_subparsers(dest="platform_admin_command", required=True)
    create_admin = admins_sub.add_parser("create", help="Créer un compte TechNova dédié")
    create_admin.add_argument("--email", required=True)
    create_admin.add_argument("--name", required=True, help="Nom complet")
    create_admin.add_argument(
        "--password-stdin",
        action="store_true",
        help="Lire le mot de passe sur l'entrée standard (sinon SM_PLATFORM_ADMIN_PASSWORD "
        "ou saisie masquée)",
    )
    create_admin.set_defaults(func=cmd_platform_admin_create)
    revoke_admin = admins_sub.add_parser(
        "revoke", help="Retirer le statut (compte fermé, sessions révoquées)"
    )
    revoke_admin.add_argument("--email", required=True)
    revoke_admin.set_defaults(func=cmd_platform_admin_revoke)
    admins_sub.add_parser("list", help="Lister les administrateurs TechNova").set_defaults(
        func=cmd_platform_admin_list
    )
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
