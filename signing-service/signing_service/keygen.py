"""Génération d'une paire de clés Ed25519 pour le Signing Service.

    signing-service-keygen --key-id technova-ed25519-2026-01 --out /srv/signing/private.pem

- La clé privée est écrite en ``0600``, **hors de tout dépôt Git** (refus sinon) et jamais
  affichée ; un fichier existant n'est jamais écrasé.
- Seule la **clé publique** est affichée, sous la forme de l'entrée à ajouter au trousseau
  versionné du backend (``backend/app/platform/licensing/data/public_keys.toml``).
"""

import argparse
import os
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from signing_service.keys import public_key_b64

KEY_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")


def inside_git_worktree(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in path.resolve().parents)


def generate(key_id: str, out: Path) -> str:
    if not KEY_ID.match(key_id):
        raise ValueError("key_id invalide")
    if inside_git_worktree(out):
        raise ValueError("refus : la clé privée ne doit jamais être écrite dans un dépôt Git")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL : jamais d'écrasement ; 0600 dès la création.
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    return (
        "[[keys]]\n"
        f'key_id = "{key_id}"\n'
        'algorithm = "Ed25519"\n'
        f'public_key = "{public_key_b64(key)}"\n'
        'status = "active"\n'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--out", required=True, type=Path, help="fichier de la clé privée")
    args = parser.parse_args(argv)
    try:
        entry = generate(args.key_id, args.out)
    except (ValueError, FileExistsError) as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 1
    print("# Entrée publique à ajouter au trousseau du backend :")
    print(entry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
