# ADR-0008 — SQLAlchemy 2 en mode synchrone

- **Statut** : Acceptée (décision TechNova du 2026-09-23)
- **Date** : 2026-09-23

## Contexte

Les opérations à venir (stock, ventes, paiements, caisse, inventaires) sont fortement
transactionnelles. La simplicité, la robustesse et la testabilité priment.

## Décision

- SQLAlchemy 2, `Session` **synchrone**, pilote `psycopg` 3.
- Endpoints FastAPI **synchrones** (`def`) lorsqu'ils accèdent à la base : ils s'exécutent
  dans le pool de threads de FastAPI.
- Pool configuré par `SM_DB_POOL_SIZE`, `SM_DB_MAX_OVERFLOW`,
  `SM_DB_POOL_TIMEOUT_SECONDS`, `pool_pre_ping`.
- Une session par requête ; **validation explicite** (`db.commit()`) dans le code
  applicatif ; tout ce qui n'est pas validé est annulé en fin de requête.
- Le contexte RLS (`app.tenant_id`, `app.user_id`) est appliqué au début de **chaque**
  transaction (événement `after_begin`) : un commit intermédiaire ne perd pas le contexte.
- Pas d'`AsyncSession`, pas de double architecture sync/async.

## Conséquences

- Code et tests simples ; pas de pièges de chargement implicite propres à l'async.
- Montée en charge par processus (workers) ; la taille du pool de threads et du pool de
  connexions doivent être dimensionnées ensemble.
- Migration vers l'async possible plus tard si des mesures réelles le justifient :
  les services reçoivent déjà leur session par injection.
