# CLAUDE.md — Consignes pour les assistants IA

Ce fichier guide Claude (et tout assistant) travaillant sur **StockManager Web**
(dépôt `GESTOCK_WEB`, éditeur TechNova). À lire avant toute modification.

## Projet

Plateforme SaaS **multi-tenant, multi-sites** de gestion commerciale.
Architecture : **Core commun + profils d'activité + modules spécialisés**.
Référence complète : [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md)
et [`docs/adr/`](docs/adr/README.md).

**Phase actuelle : 0 — fondations.** Aucune fonctionnalité métier n'est implémentée.

## Méthode de travail

- **Analyser avant de modifier.** Ne rien supposer : lire le code et la doc concernés.
- **Attendre la validation explicite** de l'utilisateur avant chaque grande phase
  (voir la feuille de route, §13 de l'architecture). Ne pas anticiper une phase.
- Ne rien supprimer sans justification explicite.
- **Tester chaque changement** (commandes ci-dessous) et rapporter les résultats tels quels.
- **Documenter les décisions importantes** dans une nouvelle ADR (`docs/adr/`).
- Le dépôt `GESTOK_ENTREP` (Desktop historique) est une **référence métier en lecture
  seule** : ne jamais le modifier ; ne pas copier son architecture technique.

## Règles d'architecture (non négociables)

1. **Le backend est la seule frontière de sécurité.** Le frontend ne fait que masquer
   pour l'ergonomie.
2. **Isolation des tenants** : `tenant_id` dérivé de l'identité authentifiée uniquement,
   filtrage systématique dans les dépôts, RLS PostgreSQL. Tout endpoint tenant-scoped
   a un test d'isolation inter-tenants.
3. Chaîne de contrôle : **User → Tenant → Site → Permission → Resource**, via des
   dépendances FastAPI composables.
4. **Jamais de `if business_type == "…"`** (ni backend, ni frontend). Tester une
   capacité : `require_module(...)`, `require_permission(...)`, feature flag.
   Les profils d'activité et les plans sont des **données**.
5. **Stock** : uniquement via `StockService` ; mouvements append-only ; stock jamais négatif
   (contrainte en base + contrôle service) ; verrouillage de lignes.
6. **Idempotence** des opérations critiques (validation de vente, paiement).
7. **Decimal** pour montants (`NUMERIC(18,2)`) et quantités (`NUMERIC(18,3)`) ;
   jamais `float` ; chaînes dans l'API.
8. **Audit** des actions sensibles.
9. **Pas de logique métier faisant foi dans React.** Le backend recalcule et valide tout.
10. Modules : dépendances déclarées dans le manifeste ; pas d'import des modèles
    internes d'un autre module ; pas de cycle.

## Structure

```text
backend/app/
  core/       config, BD, sécurité, logs (aucune règle métier)
  api/v1/     agrégation des routeurs
  platform/   tenants, sites, users, auth, RBAC, plans, profils, registre, capacités, audit
  modules/    modules métier : <module>/{manifest,router,schemas,service,repository,models}.py
  shared/     types valeur, identifiants, erreurs
frontend/src/
  app/        providers, routeur
  core/       client API, auth, capacités, registre de modules
  modules/    un dossier par module (même code que le backend)
  shared/     composants UI et utilitaires génériques
  pages/      pages hors module
docker/       Dockerfiles ; docker-compose.yml à la racine
docs/         architecture/ et adr/
```

## Commandes

```bash
# Backend (depuis backend/)
uv sync
uv run uvicorn app.main:app --reload --port 8000
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app

# Frontend (depuis frontend/)
npm install
npm run dev          # proxy /api -> http://localhost:8000
npm run lint && npm run format:check
npm run typecheck
npm test
npm run build

# Stack complète
docker compose up --build
```

## Conventions

- Code (identifiants, noms de fichiers) en **anglais** ; documentation, messages
  utilisateur et commits descriptifs en **français** acceptés.
- API REST sous `/api/v1`, ressources au pluriel, erreurs au format Problem Details (RFC 9457).
- Permissions nommées `module.ressource.action` (ex. `stock.movement.create`).
- Variables d'environnement : `SM_*` (backend), `VITE_*` (frontend). Ne jamais commiter de `.env`.
- Migrations Alembic : une par changement de schéma, jamais modifiée après fusion.
- Versions épinglées délibérément : PrimeReact 10 (MIT) et TypeScript 6.0 — voir ADR-0005
  avant toute montée de version.
