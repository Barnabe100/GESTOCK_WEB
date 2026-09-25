# ADR-0023 — Point de vente : interface au-dessus des services métier, encaissement en une étape idempotent

- **Statut** : Proposée (implémentée et testée en Phase 3.0 ; validation TechNova en attente)
- **Date** : 2026-09-25

## Contexte

La Phase 3.0 livre un point de vente (POS) générique : recherche d'articles, panier, client
facultatif, paiements (aucun, partiel, multiples), validation, confirmation. Toute la logique
de vente existe déjà : `SaleService` (prix du catalogue, stock via `StockService`, limite de
crédit), `PaymentService` (surpaiement, idempotence, caisse pour les espèces), créances
calculées, audit. Le module `pos` était déclaré « planned » (dépendant de `sales`,
`payments` et `cash_register`).

## Décision

1. **Le POS n'a aucune logique métier propre.** Module `pos` (dépend de `sales`, `catalog`,
   `stock` ; **pas** de la caisse ni des paiements électroniques planifiés) : une permission
   `pos.terminal.use` (écriture) et deux routes qui orchestrent l'existant.
2. **Encaissement en une étape** : `POST /api/v1/pos/checkout` → `SaleService.checkout`
   (module Ventes, réutilisable par d'autres canaux) = `create` puis `validate(payments)` dans
   **la transaction de la requête** : prix relus au catalogue, totaux recalculés, stock
   (`StockService`), limite de crédit (verrou du client), paiements (`PaymentService` ; caisse
   seulement pour `CASH`, via le port de l'ADR-0022), audit. Toute erreur annule tout : aucun
   brouillon orphelin, aucun stock, paiement ou mouvement de caisse partiel. Le panier du
   navigateur n'envoie que site, client, articles / quantités et paiements : aucun prix, aucun
   total.
3. **Idempotence** : clé fournie par le client pour chaque panier (`sales.idempotency_key`,
   unique par tenant). Les soumissions d'une même clé sont sérialisées
   (`pg_advisory_xact_lock`) ; la seconde renvoie la vente déjà enregistrée (`200`,
   `replayed: true`) — une vente, un paiement par moyen, un mouvement de caisse.
4. **Droits cumulés** : `pos.terminal.use` n'ouvre aucun droit de vente ; l'encaissement exige
   aussi `sales.sale.create` et `sales.sale.validate`, et `sales.payment.create` s'il comporte
   un paiement. Rôles de base : Vendeur et Gestionnaire (`pos.*`) ; Consultant : aucun accès
   (`*.view` ne donne pas `pos.terminal.use`). Nature `write` : bloqué par un abonnement
   expiré (politique existante).
5. **Recherche d'articles** : `GET /api/v1/pos/articles?site_id&search&limit≤50` =
   `stock.api.list_levels` (recherche serveur référence / désignation / code-barres, site
   visible) + prix du catalogue (`catalog.api`) ; articles inactifs renvoyés en dernier,
   signalés, jamais vendables (le serveur le refuse aussi).
6. **Canal de vente** : `sales.channel` (`BACKOFFICE` par défaut, `POS`) — même règles
   partout, dimension de reporting ; `GET /sales?channel=POS` sert l'historique récent du POS
   (aucune table ajoutée). Migration `0012` : `channel` et `idempotency_key` sur `sales`.
7. **Vente à crédit** : règles des créances inchangées (ADR-0021). L'interface avertit qu'une
   vente non soldée sans client reste une créance sans débiteur identifié (règle 2.8
   conservée).
8. **Reporting futur** préservé : `created_by` (saisie) et `validated_by` restent distincts,
   site, date, lignes, paiements (moyen, caisse, session), canal. Un futur « serveur /
   responsable de la commande » (restauration) viendra s'ajouter sans écraser l'utilisateur
   technique.

## Conséquences

- Le POS et les ventes classiques partagent exactement les mêmes services, contrôles et audit
  (`sale.created` porte `channel`).
- Le module `pos` peut être désactivé sans effet sur les ventes classiques.
- Ticket / reçu : la confirmation affiche les données renvoyées par le serveur
  (`CheckoutOut`) ; c'est le point d'extension d'une future impression (non développée).

## Alternatives écartées

- **Deux appels depuis le navigateur** (créer le brouillon, puis valider) : un échec de
  validation laisse un brouillon ; une double soumission crée deux ventes.
- **Table de ventes POS** : duplication de `sales` / `sale_lines` / `payments`.
- **Totaux calculés par le navigateur** : jamais source de vérité (règle 9).
- **POS dépendant de la caisse** : empêcherait les ventes non espèces sans caisse.
