"""Déclaration des étapes d'onboarding (sans dépendance à la base ni au contexte de requête).

Une étape est déclarée par le manifeste du module qui la porte (``ModuleManifest.onboarding``)
et collectée par le registre des modules : ajouter une étape = la déclarer dans un manifeste,
sans toucher au service. Une étape n'apparaît que si son module est effectif pour le tenant
(et si sa condition d'applicabilité est vraie).

Statuts (V1) : ``NOT_STARTED`` → ``IN_PROGRESS`` → ``COMPLETED``, dans cet ordre uniquement ;
``COMPLETED`` est définitif (fait historique du parcours d'installation, jamais régressé).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.platform.tenancy.models import Tenant


class OnboardingStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {
    OnboardingStatus.NOT_STARTED: 0,
    OnboardingStatus.IN_PROGRESS: 1,
    OnboardingStatus.COMPLETED: 2,
}

STEP_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")


@dataclass(frozen=True)
class OnboardingEnv:
    """Ce qu'une règle d'étape peut consulter. La session est dans le contexte RLS du tenant :
    une règle ne voit que ses données."""

    db: "Session"
    tenant: "Tenant"
    # Modules effectifs du tenant.
    modules: frozenset[str]
    # Limite d'une quantité plafonnable par le plan (``None`` : illimitée).
    limit: Callable[[str], int | None] = field(compare=False)


@dataclass(frozen=True)
class OnboardingAction:
    """Action proposée pour une étape : écran de l'interface web et permission nécessaire (le
    backend applique la sienne sur l'écran ciblé ; celle-ci ne sert qu'à dire si l'action est
    disponible pour l'utilisateur)."""

    route: str
    label: str  # clé i18n
    permission: str


@dataclass(frozen=True)
class OnboardingStepDef:
    code: str
    order: int
    required: bool
    title: str  # clé i18n
    description: str  # clé i18n
    # Règle de validation automatique : statut constaté à partir des données réelles.
    evaluate: "Callable[[OnboardingEnv], OnboardingStatus]" = field(compare=False, hash=False)
    action: OnboardingAction | None = None
    # Étape proposée seulement si vraie (ex. plan autorisant plusieurs utilisateurs).
    applicable: "Callable[[OnboardingEnv], bool] | None" = field(
        default=None, compare=False, hash=False
    )


def status_from(done: bool, started: bool = False) -> OnboardingStatus:
    if done:
        return OnboardingStatus.COMPLETED
    return OnboardingStatus.IN_PROGRESS if started else OnboardingStatus.NOT_STARTED
