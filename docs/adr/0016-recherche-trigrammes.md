# ADR-0016 — Recherche « contient » servie par des index trigrammes (pg_trgm)

- **Statut** : Acceptée (Phase 2.3, 2026-09-24)
- **Date** : 2026-09-24

## Contexte

Les listes utilisent une recherche « contient » insensible à la casse (`ILIKE '%terme%'`,
`app/shared/pagination.py`). Sur la table des clients, cette recherche servira la caisse et
le POS : réponse rapide attendue alors que le volume grandit (milliers de clients par
entreprise). Un index B-tree ne sert pas un motif commençant par `%` : chaque recherche
parcourrait toute la table.

## Décision

- Installer l'extension PostgreSQL **`pg_trgm`** (migration `0006`, `CREATE EXTENSION IF NOT
  EXISTS`). Elle est « trusted » depuis PostgreSQL 13 : le propriétaire de la base peut
  l'installer, sans superutilisateur ; le rôle applicatif n'est pas concerné.
- Créer un **index GIN `gin_trgm_ops` par colonne recherchée** (clients : code, nom, raison
  sociale, téléphones, email). La requête reste un `ILIKE` ordinaire ; PostgreSQL combine
  les index (`BitmapOr`), vérifié par `EXPLAIN`.
- Pas de moteur de recherche externe ni de colonne de recherche dénormalisée.
- Les autres listes (articles…) adopteront le même schéma quand leur volume le justifiera.

## Conséquences

- Recherche sans parcours complet de la table au-delà de quelques milliers de lignes
  (terme d'au moins 3 caractères ; en dessous, PostgreSQL peut choisir un parcours).
- Index légèrement plus coûteux en écriture : acceptable pour un référentiel.
- L'extension est conservée au retour arrière de la migration (d'autres objets pourront en
  dépendre).

## Alternatives écartées

- **Recherche plein texte (`tsvector`)** : orientée mots entiers, inadaptée aux codes et
  fragments de numéros.
- **Moteur externe** (Elasticsearch…) : complexité d'exploitation disproportionnée.
