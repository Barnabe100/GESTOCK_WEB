# ADR-0050 — Cuisine : postes de préparation, routage des lignes, priorités, écran cuisine

- **Statut** : Acceptée (palier R6, module `restaurant.kitchen`)
- **Date** : 2026-11-01
- **Prolonge** : [ADR-0049](0049-restauration-commandes.md) (D1, D8, convention des permissions
  R2-B, critères d'acceptation R6) ; détail : [`RESTAURANT.md`](../architecture/RESTAURANT.md)

## Contexte

Les commandes (`restaurant.orders`) disposent d'un flux de préparation générique par ligne
(reçue → en préparation → prête → servie), d'un historique et d'un avis « commande prête ».
Il manque les postes de préparation d'un site, le routage des lignes vers ces postes, une
priorité et un écran dédié à la cuisine. ADR-0049 a fixé le cadre : module facultatif, jamais
requis par les commandes ; un site sans poste reste inchangé ; un avis par poste ; désactivation
refusée tant que des lignes routées vers un poste ne sont pas finales.

## Décisions

### K1 — Données du module (migration 0049)

- `restaurant_prep_stations` : poste d'UN site (nom unique par site, insensible à la casse ;
  ordre d'affichage ; jamais supprimé, désactivation et réactivation de la même ligne).
- `restaurant_station_routes` : affectation d'une **section** OU d'un **élément** du menu du site
  à un poste (exactement l'un des deux, une affectation au plus par section et par élément ; clés
  étrangères composites du même site). Retirer une affectation supprime sa ligne (configuration,
  ancienne valeur dans l'audit).
- **Écart assumé avec l'esquisse d'ADR-0049** (`station_id` sur les éléments et les sections du
  menu) : les affectations appartiennent au module Cuisine, qui n'écrit jamais dans les tables du
  menu (frontières des modules, CLAUDE.md règle 10) ; désactiver la cuisine laisse le menu
  intact. Affectation des membres aux postes (`restaurant_station_members`) **reportée** : le
  filtre de l'écran par poste suffit en V1 et n'est jamais un droit.
- `restaurant_order_lines.station_id` (facultatif, clé étrangère composite du même site) et
  `restaurant_orders.priority` (`NORMAL` | `HIGH` | `URGENT`, défaut `NORMAL`) : colonnes du
  module des commandes, écrites **par le seul service des commandes** (ports ci-dessous).
- Évènements `PRIORITY_CHANGED` et `LINES_ROUTED` (historique en ajout seul).

### K2 — Routage

- À l'insertion des lignes (création, ajout), si `restaurant.kitchen` est **effectif** sur le
  site (recalculé après le verrou du site) : poste de l'**élément**, sinon de sa **section**,
  sinon aucun (« Sans poste », flux général) ; un poste désactivé est ignoré. Le poste est posé
  UNE fois sur la ligne (une colonne : une ligne n'est jamais dans deux files) ; un ajout rejoué
  (clé d'idempotence) ne route rien de nouveau. L'évènement de création ou d'ajout garde le poste
  de chaque ligne.
- Correction manuelle (`restaurant.kitchen.line.route`) : lignes non finales d'une commande
  ouverte, vers un poste ACTIF du même site ou « Sans poste » ; motif facultatif ; évènement
  `LINES_ROUTED` et audit.
- Port `station_port` déclaré par les commandes et implémenté par la cuisine (comme les tables) :
  les commandes ne dépendent jamais de la cuisine.

### K3 — Priorité

`NORMAL` < `HIGH` < `URGENT`, portée par la commande (une commande, une urgence). Modification
réservée à `restaurant.kitchen.priority.set` (commande ouverte), motif facultatif, évènement
`PRIORITY_CHANGED` et audit. Classement de l'écran : priorité décroissante, PUIS ancienneté (la
plus ancienne d'abord) ; l'ancienneté reste affichée séparément (heure de réception, temps
écoulé) — la priorité ne la remplace jamais.

### K4 — Écran cuisine et suivi

- Un **ticket** = une commande ouverte × un poste (ou « Sans poste ») : ses lignes non finales.
  Colonnes : « En attente » (une ligne au moins reçue), « En préparation », « Prêtes » (état de
  la ligne la MOINS avancée du ticket). Filtre par poste (tous, un poste, sans poste).
- Actions : celles des commandes (`start`, `ready`, `revert` par ligne ou par ticket, permission
  `restaurant.orders.order.prepare`) ; « Servir » reste au service (écran des commandes,
  `restaurant.orders.order.serve`). Paiement à la commande non réglé : ticket signalé, préparation
  refusée par le serveur (`order_not_settled`).
- Une commande n'est « prête » que lorsque TOUTES ses lignes actives le sont (`prep_status`
  inchangé : ligne la moins avancée).
- Avec des lignes routées : une action groupée produit **un évènement par poste** et l'avis
  « prête » est émis **par poste** dès que toutes les lignes actives de ce poste sont prêtes
  (D8) ; sans aucune ligne routée : comportement inchangé (un avis quand toute la commande est
  prête).
- Actualisation par interrogation toutes les 10 s (aucun WebSocket en V1).

### K5 — Permissions (nature) et modèles de rôles

| Permission | Nature | Admin. | Gestionnaire | Vendeur | Serveur | Préparateur | Consultant |
|---|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `restaurant.kitchen.station.view` (postes, affectations, écran avec `order.view`) | read | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `restaurant.kitchen.station.manage` (postes, affectations) | admin | ✓ | ✓ | | | | |
| `restaurant.kitchen.line.route` (correction du poste d'une ligne) | write | ✓ | ✓ | ✓ | | ✓ | |
| `restaurant.kitchen.priority.set` | write | ✓ | ✓ | ✓ | ✓ | | |

Jamais de test d'un nom de rôle ; un module planifié n'accorde aucune permission.

### K6 — Verrous et intégrité

Ordre : verrou du site (partage de clé) → réglages → commande → lignes → postes (partage, au
routage). Désactiver un poste le prend en EXCLUSIF puis compte ses lignes non finales
(`409 station_has_open_lines`). Correction : poste en partage → commande → lignes. Aucun cycle.
Désactivation du module refusée avec des lignes routées non finales
(`module_has_open_operations`, empreinte du module) ; postes et affectations conservés.

### K7 — Livraison

Module `planned` pendant le développement (registre de test), puis `available` avec une
migration qui remet à `false` ses activations héritées sur les sites existants (D10).

## Conséquences

- Un site sans cuisine (ou sans aucune affectation) fonctionne exactement comme avant.
- La consommation des ingrédients, les recettes et le QR restent hors de ce palier.
