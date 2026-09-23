# ADR-0001 — Monolithe modulaire FastAPI + SPA React

- **Statut** : Proposée
- **Date** : 2026-09-23

## Contexte

StockManager Web doit couvrir de nombreux secteurs via un Core commun et des modules
spécialisés, avec une équipe réduite, des transactions fortes (vente ⇒ stock ⇒ caisse)
et plusieurs clients (web, mobile, menu QR).

## Décision

- Backend : **un seul service FastAPI**, organisé en modules isolés
  (`core/`, `platform/`, `modules/<module>/`), chacun déclarant un manifeste.
- Frontend : **une SPA React** (Vite) dont les modules miroir sont chargés à la demande.
- Contrat entre les deux : **API REST versionnée** (`/api/v1`) décrite par OpenAPI.
- Une seule base PostgreSQL.

## Conséquences

- Transactions ACID simples entre modules (vente + stock + caisse dans une transaction).
- Déploiement et exploitation simples.
- Discipline requise sur les frontières de modules (dépendances déclarées, services
  publics, contrôle automatisé des imports).
- Un module pourra être extrait en service séparé plus tard si un besoin réel apparaît.

## Alternatives écartées

- **Microservices** : complexité opérationnelle et transactions distribuées
  injustifiées à ce stade.
- **Une application par secteur** : duplication, maintenance multipliée — explicitement exclu.
- **Rendu serveur (SSR)** : inutile pour un back-office authentifié ; le menu QR
  public pourra avoir un point d'entrée léger dédié.
