# ADR-0015 — RBAC : rôles de base, rôles personnalisés, portée et anti-escalade

- **Statut** : Acceptée (validée le 2026-09-24)
- **Date** : 2026-09-24

## Contexte

StockManager doit servir des secteurs très différents (alimentation, quincaillerie,
restaurant, maquis…). Une liste fermée de rôles ne peut pas couvrir leurs organisations
(Magasinier, Caissier, Responsable magasin, Serveuse, Chef de cuisine…). L'analyse du RBAC
existant a aussi révélé deux failles d'escalade :

1. l'anti-escalade comparait avec les permissions du **site sélectionné** : un administrateur
   limité à un site pouvait accorder des droits sur tout le tenant ;
2. la gestion des membres ne limitait pas les **sites accordés** au périmètre de l'acteur.

## Décision

**Principe.** Utilisateur → appartenance → rôle(s) (tenant ou site) → permissions → action.
Les permissions sont la seule source d'autorisation ; aucun code ne teste un nom ou un code de
rôle. Autorisation effective :

```
(propriétaire ? toutes : ∪ permissions des rôles ACTIFS valables sur tout le tenant ou sur le site sélectionné)
  ∩ permissions des modules effectifs (profil ∩ plan ∩ activations)
  ∩ natures autorisées par le statut d'abonnement
  + fonctionnalités du plan + site accessible + site de la ressource
```

**Rôles de base** (rôles système, `role_templates.toml`, résolus à l'exécution — ADR-0013) :

| Code | Nom | Contenu |
|---|---|---|
| `administrator` | Administrateur | `*` ; **protégé** (ni désactivable, ni modifiable) |
| `manager` | Gestionnaire | catalogue, fournisseurs, stock sans annulation ni gestion des motifs, alertes |
| `seller` | Vendeur | consultation des articles, catégories, stock, alertes ; droits de vente ajoutés avec le module Ventes |
| `viewer` | Consultant | `*.view` sauf `users.*`, `audit.*`, `organization.module.*` |

Aucune permission de module non développé n'est déclarée.

**Rôles personnalisés** : propres au tenant ; création, modification (nom, description,
permissions), duplication (depuis un rôle de base ou personnalisé), activation /
désactivation, consultation des membres, attribution (tenant ou site). Nom unique par tenant
**sans distinction de casse** et distinct des noms des rôles de base. Deux tenants peuvent
avoir un rôle de même nom aux permissions différentes.

**Jamais de suppression.** `DELETE /roles` n'existe plus ; le rôle applicatif n'a plus le
droit `DELETE` sur `roles`. Désactiver un rôle : il n'accorde plus rien et ne peut plus être
attribué ; ses attributions sont **conservées** et la réactivation rétablit les droits. Si le
rôle est attribué, l'API répond `409 role_in_use` (membres listés) tant que la désactivation
n'est pas confirmée (`{"confirm": true}`).

**Anti-escalade par portée** (non-propriétaire) :
- créer ou modifier un rôle, l'activer, le désactiver, ou l'attribuer **sur tout le tenant**
  exige de détenir ses permissions **sur tout le tenant** ;
- l'attribuer **pour un site** exige de les détenir sur ce site, qui doit être accessible ;
- les sites accordés à un membre (et ceux qu'il a déjà, pour le modifier) doivent être dans
  les sites de l'acteur ; « tous les sites » exige que l'acteur l'ait (`site_escalation`).

**Propriétaire** (`is_owner`) : hors RBAC, administrateur de dernier recours, non modifiable ;
personne ne modifie ses propres accès.

**Audit** : `role.created` (dont `copied_from`), `role.updated` (avant/après, permissions
ajoutées/retirées), `role.activated`, `role.deactivated` (membres concernés),
`member.role_assigned`, `member.role_removed`, `member.updated` (avant/après).

## Conséquences

- Nouveaux secteurs et modules : de nouvelles permissions et, au besoin, un modèle de rôle de
  base enrichi — jamais de refonte du RBAC ni de migration par tenant.
- Un rôle personnalisé ne contourne ni le plan, ni les modules, ni l'abonnement, ni les sites.
- Migration `0005` : `stock_manager` → `manager`, `viewer` renommé Consultant, Vendeur ajouté ;
  les quatre rôles de base garantis dans chaque tenant ; identifiants et attributions
  inchangés.

## Alternatives écartées

- **Rôles figés** (liste fermée) : ne couvre pas les organisations des secteurs visés.
- **Suppression physique des rôles non attribués** : perte de l'historique d'autorisation.
- **Rôles de base modifiables par le tenant** : divergence avec les mises à jour TechNova ;
  la duplication couvre le besoin de variante.
