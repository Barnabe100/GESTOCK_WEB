# ADR-0049 — Restauration : menu, moteur de commandes unique, préparation, règlement, QR

- **Statut** : Acceptée (conception, palier R0) — **aucun module livré, aucun code**
- **Date** : 2026-10-09
- **Prolonge** : [ADR-0020](0020-paiements-des-ventes.md) (paiements),
  [ADR-0022](0022-caisse.md) (caisse), [ADR-0023](0023-point-de-vente.md) (POS),
  [ADR-0030](0030-delegation-rbac.md) (délégation), [ADR-0037](0037-encaissement.md)
  (encaissement), [ADR-0045](0045-lots-et-peremption-stock-reception.md) (lots),
  [ADR-0046](0046-assortiment-par-site.md) (assortiment), [ADR-0047](0047-recu-de-vente-pos.md)
  (reçu), [ADR-0048](0048-changement-profil-site.md) (profil et modules par site, dont la
  décision ouverte d'E.1)
- **Détail** : [`RESTAURANT.md`](../architecture/RESTAURANT.md) (modèle, verrous, permissions,
  migrations, tests, critères d'acceptation R1 à R9)

## Contexte

Les modules `restaurant.*` sont déclarés `planned` (`backend/app/modules/planned.py`) : visibles
au catalogue, jamais activables (palier E.1, `422 module_not_implemented`). Le socle par site
(profil, `site_modules`, capacités, empreintes, changement de profil) est consolidé (palier G).

Le domaine Restauration / Maquis doit couvrir le petit maquis (sans table, sans cuisine, une
seule personne) jusqu'au restaurant structuré (salles, postes de préparation, QR), en
réutilisant catalogue, assortiment, ventes, paiements, caisse, stock, audit, RBAC et RLS, sans
système parallèle. Les arbitrages métier A1 à A7, B1 à B6, Z1 à Z3 et W1 sont intégrés
ci-dessous.

## Décision

### D1 — Modules (codes conservés)

| Code | Rôle | Dépendances obligatoires |
|---|---|---|
| `restaurant.menu` | sections, éléments, disponibilité manuelle | `catalog` |
| `restaurant.orders` | moteur unique, préparation générique, attribution, règlement | `restaurant.menu`, `sales` |
| `restaurant.tables` | zones et tables (facultatif) | `restaurant.orders` |
| `restaurant.kitchen` | affiché « Postes de préparation » (facultatif ; postes = données du site) | `restaurant.orders` |
| `restaurant.qr` | commandes publiques par QR (STANDARD et ENTREPRISE) | `restaurant.orders` |
| `restaurant.recipes` | recettes (ultérieur) | `catalog`, `stock`, `sales` |

- La dépendance actuelle `restaurant.orders → restaurant.tables` est supprimée à la livraison
  de R1. Aucun module n'est rendu `available` avant sa livraison réelle.
- Les dépendances facultatives (table, poste, caisse, client) sont testées par capacité côté
  serveur, jamais par un test de profil.
- `restaurant.kitchen` n'est **jamais requis** par les commandes (sans postes : flux de
  préparation générique). Il est activé ou désactivé site par site par
  `organization.module.manage`, dans les limites de l'offre, de la disponibilité et des
  dépendances ; désactivation refusée tant que des lignes routées vers un poste ne sont ni
  servies ni annulées (`409 module_has_open_operations`) ; les postes configurés sont conservés.

### D2 — Un moteur, trois canaux

Personnel, POS et QR créent la même Commande ; le canal (`STAFF` / `POS` / `QR`) n'est qu'une
donnée d'origine, posée par le serveur selon la route appelée. Table, responsable et client sont
facultatifs ; le mode de service (sur place / comptoir / à emporter) est obligatoire. Numéro court
par site et par jour (fuseau de l'entreprise, ADR-0028), recherche par numéro. Le mode de
paiement du site est recopié sur la commande à sa création : changer le réglage ne touche aucune
commande existante.

### D3 — Deux axes et un cycle de vie

- **Préparation** (lignes) : reçue → en préparation → prête → servie/remise (un seul état
  technique, libellé « Servie » sur place, « Remise » au comptoir ou à emporter) ;
  prête → en préparation permis (correction). **Servie/remise est définitif** : ni retour
  arrière, ni annulation, ni modification silencieuse (contrôle du service ET déclencheur en
  base).
- **Règlement** : non réglée → réglée. **Réglée = vente issue de la commande validée** (payée ou
  à crédit selon les règles existantes). L'état financier (payé, partiel, dû) est **toujours lu
  sur la vente** ; la commande n'en garde aucune copie.
- **Cycle de vie** : à confirmer (QR) → ouverte → close | annulée | refusée. Close = réglée ET
  toutes les lignes non annulées servies/remises. **Close est définitif** : aucune réouverture.
- **Commande réglée : ni ajout ni annulation de lignes** (`409 order_settled`) ; seules les
  transitions de préparation et de service continuent ; une commande supplémentaire est une
  nouvelle commande.
- Lignes immuables sauf leur état ; correction = annulation d'une ligne + nouvelle ligne
  (commande non réglée).

### D4 — Prix figés

Prix du catalogue global (article, ou conditionnement ; conditionnement sans prix configuré
exclu) lu et figé par le serveur sur la ligne à sa création. La vente issue d'une commande porte
une origine posée par le serveur, **immuable** ; seule cette origine dispense du contrôle
`sale_prices_changed`. Ventes ordinaires et POS inchangés. Au plus **une vente active** par
commande ; un nouveau règlement reprend les prix figés.

### D5 — Stock

Déduction à la validation de la vente seulement, par le moteur existant (FEFO, lots, CMUP).
Aucune réservation ; disponibilité indicative + épuisé manuel. Refus au règlement : articles
nommés, commande conservée, nouvelle tentative après correction.

### D6 — Paiement avant ou après (A1, B2, B6)

Mode du site (défaut = donnée du profil, recopié à l'activation, jamais écrasé par un changement
de profil, modifiable par le gestionnaire, aucune dérogation par commande en V1) :

- « à la commande » : le règlement précède la préparation (`409 order_not_settled`) ;
- « à la fin » : règlement à tout moment (avant, pendant ou après la préparation et le service).

Deux transactions distinctes :

- **T1 Création** : numéro, prix figés ; ni vente, ni paiement, ni stock, aucun contrôle de stock.
- **T2 Règlement** (tout ou rien) : création de la vente (origine = commande, canal
  `RESTAURANT`) → **validation** de la vente = déduction du stock → **paiements** (espèces :
  session ouverte de l'encaisseur ; reste dû : crédit — client, `sales.sale.credit_create`,
  limite et dérogation existantes). La dérogation existante `expired_lot_override` (permission
  `sales.sale.expired_lot_override`, motif, audit) est acceptée comme pour `checkout`.

Échec de T2 (stock, lot, article inactif ou hors assortiment, session de caisse, moyen de
paiement, limite de crédit) : seule T2 est annulée ; la commande reste intacte, « à régler »,
retrouvable par son numéro ; nouvelle tentative après correction. La commande précède toujours
le paiement : « avant » signifie avant la préparation ; aucun acompte ni paiement sans vente.
Aucun paiement en ligne en V1. `POST /pos/checkout` et `SaleService.checkout` sont inchangés.

### D7 — Attribution

- Protection d'une commande prise : 5 min par défaut, réglable par site (0 = désactivée) ;
  réattribution immédiate par `restaurant.order.reassign`, motif obligatoire, auditée ; le
  nouveau responsable doit détenir `restaurant.order.claim` effectif sur le site
  (`422 assignee_not_eligible`).
- Délai entre deux prises d'un même employé : désactivé par défaut, réglable par site,
  indépendant ; seule l'action explicite « Prendre » compte (ni la création, ni une
  réattribution) ; dernière prise lue dans l'historique de la commande (aucune table) ; prises
  d'un même employé sur un site sérialisées sous verrou.
- Contrôles serveur sous verrou transactionnel : verrou de la commande → relecture du
  responsable et de l'heure de prise → relecture du réglage du site → contrôle des permissions →
  action. Conflit : `409`. Deux prises simultanées : exactement une réussit. Appels directs à
  l'API : mêmes contrôles.

### D8 — Notifications internes (A4)

Mécanisme de plateforme générique (`member_notifications`), distinct des rappels d'abonnement.

- Transition « prête » : le responsable ET les membres du site disposant **effectivement** de
  `restaurant.order.serve` (calcul par `CapabilityService.resolve` : appartenance active, accès
  et rôles du site, abonnement du site), **moins l'auteur de l'action, toujours** ; tout
  destinataire doit pouvoir lire la commande (`restaurant.order.view` effectif sur ce site).
- Sans postes : un avis quand toute la commande est prête ; avec postes : un avis par partie
  prête et par poste (`Commande #125 — Table 2 — Bar : prêt (2 articles)`).
- Clé anti-doublon = (destinataire, évènement de transition) ; une transition répétée légitime
  (lignes ajoutées, correction) crée un nouvel évènement. Une seule notification par
  destinataire et par évènement.
- Résolution **calculée à la lecture** (lignes concernées servies, remises ou annulées) : aucune
  écriture sur les notifications des autres, historique intact.
- Lu / non lu individuel, compteur, « tout marquer lu », actualisation par interrogation toutes
  les 10 à 15 s (aucun WebSocket / SSE en V1, évolutif), purge à 30 jours (rôle et mécanisme
  définis en R4). Lisibles par leurs seuls destinataires ; jamais un autre site ni une autre
  entreprise.

### D9 — QR (B1)

Jeton aléatoire opaque par point d'accès (table ou comptoir), stocké haché, révocable par
rotation ; entreprise et site déduits du jeton seulement ; menu public sans stock, coût ni donnée
interne ; navigateur seul, aucune installation. Confirmation explicite par défaut
(`restaurant.order.confirm`), acceptation automatique facultative par site ; en mode « à la
commande », le comptoir **confirme puis règle** (deux évènements, deux contrôles de permission,
enchaînables sur un même écran). Limitation de fréquence persistante, idempotence, plafonds,
RLS. Suivi public « à régler » sans aucune donnée de stock. Notifications push sur le téléphone
du client : volet mobile ultérieur, avec consentement, jamais obligatoires ; le suivi Web
fonctionne toujours.

### D10 — Activation à la livraison

Sur les sites existants, un module livré reste désactivé jusqu'à activation explicite : sa
migration de livraison remet à `false` les activations héritées de la période `planned`
(descente sans restauration : ces lignes étaient inertes). C'est le traitement, module par
module, de la décision ouverte d'E.1 (ADR-0048). Nouveaux sites : défauts du profil dans les
limites de l'offre. Un module non livré n'est jamais utilisable.

### D11 — Profils (données)

Définitions : *proposé* = `modules` ∪ `optional_modules` du profil ; *activé par défaut* =
`modules` (écrit activé à la création d'un site, s'il est dans l'offre) ; *facultatif* =
`optional_modules` (écrit désactivé, activable par le gestionnaire) ; *effectif* = proposé ∩
offre ∩ activé ∩ disponible ∩ dépendances effectives — seul état qui ouvre l'accès.

| Profil | Activés par défaut | Facultatifs | Paiement par défaut |
|---|---|---|---|
| Maquis, Bar, Café | menu, orders | tables, kitchen, qr, recipes | à la fin |
| Boulangerie | menu, orders | tables, kitchen, qr, recipes | à la commande |
| Restaurant, Pizzeria | menu, orders, tables, kitchen | qr, recipes | à la fin |
| Traiteur, Restauration rapide | menu, orders, kitchen | tables, qr, recipes | à la commande |

`restaurant.recipes` reste « à venir » jusqu'à R8 ; `restaurant.qr` entre dans l'offre STANDARD
à la livraison de R7. Le paiement par défaut est porté par une section générique
`module_settings` des profils (aucun test de code de profil).

### D12 — RBAC (A6, B5, Z2)

- Union des rôles de l'utilisateur pour le site ; chaque action teste SA permission, jamais un
  nom de rôle. Fonction dans la commande (responsable, préparateur d'une ligne, personne qui
  sert / remet) ≠ rôle RBAC ; une même personne peut cumuler les trois.
- Encaisser = `sales.sale.create` + `sales.sale.validate` (+ `sales.payment.create`,
  `sales.sale.credit_create` selon le cas) sur le site, et règles de session de caisse
  existantes.
- Modèles facultatifs **Serveur** (aucune permission d'encaissement) et **Préparateur** :
  **créés à la demande** (`POST /roles/from-template`), jamais automatiquement dans les
  entreprises ; clé générique `auto_provision = false` dans `role_templates.toml` ; gestion des
  conflits de noms précisée en R2.
- Permissions nouvelles déclarées avec leur nature ; délégation calculée par le serveur
  (ADR-0030) ; l'annulation d'une vente reste `sales.sale.cancel` / `sales.payment.cancel`
  (Administrateur par défaut, attribuables par rôle personnalisé).

### D13 — Annulations (A2, A3, B3, B4, Z1, Z3, W1)

- Aucune expiration automatique en V1.
- Commande **non réglée** : ligne reçue → `restaurant.order.cancel` ; ligne en préparation ou
  prête → `restaurant.order.cancel_prepared` ; motif obligatoire ; sans effet sur vente,
  paiement, caisse ou stock (rien n'a été vendu ni sorti).
- Commande : annulation de toutes ses lignes non finales (permission selon leur état), motif
  obligatoire ; refusée si réglée (`409 order_settled`) ou si une ligne est servie
  (`409 order_has_served_lines`).
- **Commande réglée non close (Z3)** : aucune annulation de ligne. Procédure : annulation de
  chaque paiement (`sales.payment.cancel` ; espèces : sortie inverse dans la session d'origine
  ouverte), puis de la vente (`sales.sale.cancel` ; un `CANCELLATION` par mouvement d'origine,
  même quantité, même coût, même lot, CMUP inchangé) par une personne habilitée ; la commande
  redevient « à régler » (évènement `SALE_CANCELLED`) ; annulation de la ligne avec motif ;
  nouveau règlement aux prix figés. Si la nouvelle vente consomme d'autres lots (FEFO, ex. lot
  d'origine devenu périmé), le stock total reste exact et le coût reste le CMUP du site (aucun
  coût par lot) ; la répartition par lot peut différer du physique et se corrige par
  l'inventaire par lot existant.
- **Commande close** : annulation de sa vente refusée (`409 order_closed`). Une erreur
  d'encaissement se corrige par l'annulation du paiement puis un nouveau paiement sur la même
  vente ; le reste dû éventuel est une créance calculée selon les règles existantes ; la
  commande reste close.
- **Servie non réglée** (client parti, B3, Z1) : règlement à crédit selon les règles existantes
  (client nommé, `sales.sale.credit_create`, limite et dérogation). À défaut de client à
  créditer, la commande **reste ouverte** — limite connue de V1 —, identifiable et avec son
  ancienneté visible ; la perte n'existe pas en V1 et sera conçue séparément.
- **Espèces après clôture de la session d'origine (W1)** : la règle de caisse existante est
  conservée (`409 cash_session_closed`) ; aucun remboursement n'est introduit en V1 ; la
  commande concernée reste identifiable (limite connue de V1).
- Historique en ajout seul et audit conservés ; contrôle du site et de l'entreprise à chaque
  action.

## Conséquences

- **Ventes (R3)** : `origin_type` / `origin_id` immuables (déclencheur), index unique partiel des
  ventes actives par origine, canal `RESTAURANT` (contrainte CHECK), méthode interne
  `create_from_order`, port `sales/origin_port.py` appelé **avant** le verrou de la vente lors
  d'une annulation (refus si aucun port enregistré : `409 sale_origin_unavailable`).
- **Catalogue** : les commandes non finales s'enregistrent dans les ports assortiment, usage des
  conditionnements et suivi par lot.
- **Plateforme** : hook `site_setup` à l'activation d'un module sur un site, section
  `module_settings` des profils, notifications de membres, compteur
  `restaurant.order:{site}:{jour}` (jamais préfixé par `{site_id}:`, qui figerait le code du
  site), clé `auto_provision` des modèles de rôles.
- **Base** : déclencheurs d'états finaux (lignes, commandes) et d'origine des ventes.
- **Licences** : un site STANDARD déjà licencié n'obtient le QR qu'à sa prochaine licence ; la
  règle de licence n'est pas modifiée.
- **Changement de profil** : commandes non finales = travail en cours (BLOCKED si
  `restaurant.orders` disparaît, commandes nommées dans le détail) ; closes = historique
  (STRONG) ; menu, tables, postes = configuration.
- **Limites connues de V1** : commandes servies non réglées sans client à créditer (Z1) ;
  remboursement d'espèces après clôture de la session d'origine (W1).

## Alternatives écartées

- Utiliser le brouillon de vente comme commande : prix relus, pas d'états de préparation, risque
  de régression du POS.
- Un moteur QR séparé : deux règles de prix, de stock et de notification.
- Tables obligatoires ; un module par poste (Cuisine, Bar…).
- Relire le prix au règlement : le client paierait un autre prix que celui annoncé.
- Réserver le stock à la commande.
- Réutiliser la table `notifications` des rappels d'abonnement.
- Jeton QR signé (irrévocable sans liste noire) ; WebSocket / SSE en V1.
- Un compte par fonction.
- Commande et paiement dans une seule transaction (la commande serait perdue à l'échec).
- Expiration automatique des commandes.
- Réouverture d'une commande close ; annulation d'une ligne réglée sans annuler la vente.
- Procédure de perte ou de remboursement improvisée.
- Création automatique des modèles Serveur et Préparateur dans toutes les entreprises.

## Suivi

Paliers R1 (Menu) à R9 (Consolidation) : [`RESTAURANT.md`](../architecture/RESTAURANT.md).
Notifications push des clients : volet mobile ultérieur. Aucun palier ne commence sans
validation explicite.
