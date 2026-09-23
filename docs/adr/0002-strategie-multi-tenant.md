# ADR-0002 — Stratégie multi-tenant : base partagée, `tenant_id`, RLS

- **Statut** : Proposée
- **Date** : 2026-09-23

## Contexte

Chaque entreprise cliente est un tenant indépendant. Un utilisateur ne doit jamais
accéder aux données d'un autre tenant. Le nombre de tenants visé est élevé
(petits commerces), avec des volumes individuels modestes.

## Décision

- **Base et schéma partagés**, colonne `tenant_id NOT NULL` (indexée, en tête des
  index composites) sur toutes les tables tenant-scoped ; `site_id` pour les données
  propres à un site.
- Défense en profondeur :
  1. `tenant_id` dérivé **uniquement** de l'identité authentifiée ;
  2. filtrage systématique dans la couche d'accès aux données ;
  3. **Row-Level Security PostgreSQL** activée dès la V1, avec
     `SET LOCAL app.tenant_id` par transaction et un rôle SQL applicatif sans
     `BYPASSRLS` et non propriétaire des tables.
- Clés étrangères composites `(tenant_id, id)` là où c'est pertinent pour empêcher
  les références croisées entre tenants.
- Tests automatisés d'isolation obligatoires pour chaque endpoint tenant-scoped.

## Conséquences

- Coût d'exploitation faible, migrations uniques pour tous les tenants.
- Une erreur applicative de filtrage est rattrapée par la RLS.
- Les tests d'intégration doivent tourner sur PostgreSQL réel (pas SQLite).
- Export / suppression des données d'un tenant par requêtes filtrées.

## Alternatives écartées

- **Un schéma par tenant** : migrations × N, pool de connexions complexe.
- **Une base par tenant** : coûteux et lourd à opérer pour de petits commerces ;
  reste envisageable plus tard pour un très gros client ENTREPRISE.
- **Filtrage applicatif seul (sans RLS)** : une seule requête oubliée suffit à fuiter.
