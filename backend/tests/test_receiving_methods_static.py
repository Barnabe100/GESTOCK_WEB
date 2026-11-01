"""R6.5 (ADR-0051) — portée internationale : aucun opérateur, aucune devise ni aucun fuseau
national codés en dur dans le code applicatif.

Les opérateurs et établissements financiers sont des DONNÉES (catalogue versionné géré par
TechNova, opérateurs personnalisés des entreprises) ; la devise et le fuseau d'une entreprise
proviennent du référentiel des pays. Les données de test et le catalogue (``*.toml``) ne sont
pas concernés.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = ROOT / "backend" / "app"
FRONTEND_SRC = ROOT / "frontend" / "src"

# Exemples de marques d'opérateurs : liste de CONTRÔLE (jamais utilisée par l'application).
OPERATOR_NAMES = re.compile(
    r"\b(orange money|moov( money)?|wave|mtn|airtel|m-?pesa|telecel|coris money|sank money|"
    r"ligdicash|wizall|free money)\b",
    re.IGNORECASE,
)
NATIONAL_DEFAULTS = re.compile(r"\bXOF\b|Africa/Ouagadougou")


def _application_files() -> list[Path]:
    files = [p for p in BACKEND_APP.rglob("*.py") if "__pycache__" not in p.parts]
    for pattern in ("*.ts", "*.tsx", "*.json"):
        files += [
            p
            for p in FRONTEND_SRC.rglob(pattern)
            if ".test." not in p.name and p.name not in {"testData.ts", "testing.tsx"}
        ]
    return files


def _offenders(pattern: re.Pattern[str]) -> list[str]:
    found = []
    for path in _application_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                found.append(f"{path.relative_to(ROOT)}:{number}")
    return found


def test_no_operator_name_is_hard_coded() -> None:
    assert _offenders(OPERATOR_NAMES) == []


def test_no_national_currency_or_time_zone_is_hard_coded() -> None:
    assert _offenders(NATIONAL_DEFAULTS) == []
