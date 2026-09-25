"""Configuration d'expérience (profils UX) : navigation, tableau de bord, terminologie, thème.

Types et fusion **purs** (aucun accès base) partagés par le chargement du catalogue, sa
synchronisation et la résolution des capacités. Aucune de ces données n'est une règle de
sécurité : elles décrivent la présentation ; les droits restent RBAC + module + plan +
abonnement + RLS (ADR-0024).
"""

import copy
import re
from dataclasses import dataclass, field
from typing import Any

# Palette contrôlée : chaque accent a une variante de jetons `--sm-accent*` au contraste
# vérifié (frontend/src/styles.css). Jamais de couleur libre.
ACCENTS = ("blue", "green", "orange", "teal", "indigo")
DENSITIES = ("comfortable", "compact")
THEME_KEYS = ("accent", "density", "icon")

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
QUALIFIED_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
# Référence d'un widget ou d'un raccourci : « <module>:<identifiant> ».
REF_RE = re.compile(r"^(?P<module>[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?):[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class NavGroup:
    """Rubrique du menu : identifiant (libellé i18n `navGroups.<group>`) et modules listés."""

    group: str
    modules: tuple[str, ...]


@dataclass(frozen=True)
class UxConfig:
    navigation: tuple[NavGroup, ...] = ()
    widgets: tuple[str, ...] = ()
    shortcuts: tuple[str, ...] = ()
    terminology: dict[str, Any] = field(default_factory=dict)
    theme: dict[str, str] = field(default_factory=dict)


def ref_module(ref: str) -> str:
    """Module d'une référence de widget ou de raccourci (``alerts:low_stock`` → ``alerts``)."""
    return ref.split(":", 1)[0]


def parse_navigation(raw: Any) -> tuple[NavGroup, ...]:
    """Navigation lue d'un fichier ou de la base : ``[{group, modules}]``. Une valeur d'un
    autre format (ancienne liste de modules, avant la Phase 3.1) est ignorée."""
    if not isinstance(raw, list):
        return ()
    groups: list[NavGroup] = []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("group"), str):
            groups.append(NavGroup(group=item["group"], modules=tuple(item.get("modules", ()))))
    return tuple(groups)


def navigation_json(navigation: tuple[NavGroup, ...]) -> list[dict[str, Any]]:
    return [{"group": g.group, "modules": list(g.modules)} for g in navigation]


def dashboard_json(widgets: tuple[str, ...], shortcuts: tuple[str, ...]) -> dict[str, Any]:
    return {"widgets": list(widgets), "shortcuts": list(shortcuts)}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Fusion récursive (l'override l'emporte) ; ni ``base`` ni ``override`` ne sont modifiés."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def merge_ux(
    base: UxConfig,
    *,
    navigation: tuple[NavGroup, ...] = (),
    dashboard: dict[str, Any] | None = None,
    terminology: dict[str, Any] | None = None,
    theme: dict[str, Any] | None = None,
) -> UxConfig:
    """Configuration d'un profil d'activité = son profil UX + ses surcharges :
    navigation et listes du tableau de bord **remplacées** si déclarées ; terminologie
    **fusionnée** en profondeur ; thème **fusionné** clé par clé."""
    dashboard = dashboard or {}
    return UxConfig(
        navigation=navigation or base.navigation,
        widgets=tuple(dashboard["widgets"]) if "widgets" in dashboard else base.widgets,
        shortcuts=tuple(dashboard["shortcuts"]) if "shortcuts" in dashboard else base.shortcuts,
        terminology=deep_merge(base.terminology, terminology or {}),
        theme={**base.theme, **(theme or {})},
    )


def validate_ux_parts(
    owner: str,
    *,
    known_modules: set[str],
    navigation: tuple[NavGroup, ...],
    dashboard: dict[str, Any],
    theme: dict[str, Any],
    terminology: dict[str, Any],
) -> list[str]:
    """Erreurs de forme d'une configuration UX (profil UX ou surcharges d'un profil)."""
    errors: list[str] = []
    seen_groups: set[str] = set()
    seen_modules: set[str] = set()
    for group in navigation:
        if not NAME_RE.match(group.group):
            errors.append(f"{owner} : rubrique de navigation invalide {group.group!r}")
        if group.group in seen_groups:
            errors.append(f"{owner} : rubrique de navigation en double {group.group}")
        seen_groups.add(group.group)
        for module in group.modules:
            if module not in known_modules:
                errors.append(f"{owner} : navigation vers un module inconnu {module}")
            if module in seen_modules:
                errors.append(f"{owner} : module {module} présent dans deux rubriques")
            seen_modules.add(module)
    for key in dashboard:
        if key not in ("widgets", "shortcuts"):
            errors.append(f"{owner} : clé de tableau de bord inconnue {key}")
    for ref in (*dashboard.get("widgets", ()), *dashboard.get("shortcuts", ())):
        if not REF_RE.match(ref):
            errors.append(f"{owner} : référence invalide {ref!r} (attendu <module>:<id>)")
        elif ref_module(ref) not in known_modules:
            errors.append(f"{owner} : {ref} référence un module inconnu")
    for key, value in theme.items():
        if key not in THEME_KEYS:
            errors.append(f"{owner} : clé de thème inconnue {key}")
        elif key == "accent" and value not in ACCENTS:
            errors.append(f"{owner} : accent {value!r} hors palette {ACCENTS}")
        elif key == "density" and value not in DENSITIES:
            errors.append(f"{owner} : densité {value!r} inconnue {DENSITIES}")
        elif key == "icon" and not (isinstance(value, str) and value.startswith("pi pi-")):
            errors.append(f"{owner} : icône {value!r} invalide (classe PrimeIcons attendue)")
    for lang, bundles in terminology.items():
        if not isinstance(bundles, dict):
            errors.append(f"{owner} : terminologie {lang} invalide")
    return errors
