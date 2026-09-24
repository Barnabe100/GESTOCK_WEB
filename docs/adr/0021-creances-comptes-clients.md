# ADR-0021 — Créances / comptes clients : calculées sans table, limite de crédit à la validation sous verrou du client

- **Statut** : Acceptée (implémentée et testée en Phase 2.8 ; validation TechNova en attente)
- **Date** : 2026-09-24

## Contexte

La Phase 2.7 a rendu l'encaissement indépendant de la validation : une vente validée peut
rester non payée ou partiellement payée. La Phase 2.8 doit permettre de consulter et de
contrôler ce que les clients doivent, et appliquer enfin `customers.credit_limit` (champ
préparatoire depuis la Phase 2.3), sans seconde source de vérité financière, sans échéancier ni
remboursement.

## Décision

1. **Aucune table `receivables`.** Une créance ouverte est une vente `VALIDATED` dont le reste
   dû est strictement positif :

   ```text
   payé   = Σ paiements COMPLETED de la vente
   reste  = total − payé
   créance ouverte ⇔ sale.status = VALIDATED ET reste > 0
   exposition(client) = Σ restes dus de ses créances ouvertes, tous sites du tenant
   ```

   Brouillon et vente annulée ne sont jamais des créances ; les paiements `CANCELLED` ne
   comptent jamais. Même définition du « payé » que l'état d'encaissement de la 2.7 ; les
   requêtes (`balances_query`, `open_receivables_query`, `customer_exposure`) vivent dans
   `sales` (`sales/credit.py`) et sont exposées par `sales/api.py`. **Aucune migration** :
   aucun changement de schéma n'est nécessaire (index existants suffisants : `sales.customer_id`,
   `sales.site_id`, `(tenant_id, sale_date)`, `payments (tenant_id, sale_id)` ; le montant payé
   est une sous-requête `LATERAL` par vente, sans N+1).
2. **Module `receivables`** (dépend de `sales`, `customers`, `stock`) : couche de consultation
   seule, permission unique `receivables.receivable.view` (nature `read` : consultable avec un
   abonnement expiré). API : `GET /receivables`, `/receivables/summary`, `/receivables/{sale_id}`
   et, comme sous-ressources du client, `GET /customers/{id}/receivables` et
   `/customers/{id}/credit-exposure`. Ces deux routes sont montées par le module Créances via
   un nouveau champ de manifeste `extra_routers` (protégées par `require_module("receivables")`),
   sans ajouter de dépendance au module Clients.
3. **Limite de crédit appliquée à la validation de la vente** (`SaleService.validate`), là où
   l'exposition naît — jamais à la création d'un brouillon. Exposition créée = total −
   encaissements immédiats. Refus (`422 credit_limit_exceeded`) si
   `exposition actuelle + exposition créée > limite`. Aucune exposition créée (vente payée
   intégralement) : aucun contrôle. Vente sans client : aucune exposition client. La limite est
   une règle de vente : elle s'applique même si le module Créances est désactivé.
4. **`credit_limit IS NULL` = limite non configurée** (aucune limite), et non « crédit
   interdit » ; `0` = aucun crédit autorisé ; limite atteinte exactement : acceptée. L'API
   renvoie `credit_limit: null`, `limit_configured: false`, `available_credit: null` — aucun
   montant fabriqué.
5. **Encaissement immédiat à la validation** : `POST /sales/{id}/validate` accepte un corps
   facultatif `{payments: [{amount, method, …}]}`. Sans lui, l'exemple « vente payée
   immédiatement, aucune exposition » serait impossible (un paiement exige une vente validée).
   Les paiements passent par `PaymentService` (mêmes règles : surpaiement refusé, audit), dans
   la même transaction ; exige aussi `sales.payment.create`. Réutilisable tel quel par le POS.
6. **Concurrence** : verrou de la ligne client (`SELECT … FOR NO KEY UPDATE`) pris après celui
   de la vente (ordre constant vente → client → niveaux de stock), avant la lecture de
   l'exposition. Deux validations à crédit du même client (même site ou non) s'exécutent l'une
   après l'autre ; la seconde voit l'exposition de la première. `NO KEY UPDATE` ne bloque ni la
   création de ventes ni les paiements (verrous `KEY SHARE` des clés étrangères) ; un paiement ou
   son annulation ne prend pas le verrou client (aucun interblocage) : le résultat équivaut
   toujours à une exécution en série.
7. **Multi-site** : les créances suivent l'accès aux ventes (sites visibles). L'exposition
   consolidée (tous sites) — celle du contrôle — n'est communiquée qu'à un membre sans
   restriction de site et sans site sélectionné (`consolidated: true`) ; sinon l'exposition ne
   couvre que ses sites, `available_credit` est nul, et le refus de validation n'indique que la
   limite et le montant à crédit de la vente.
8. **Vente validée sans client avec reste dû** : listée (« Sans client ») pour ne pas masquer
   un encaissement manquant ; comptée dans le total dû, pas dans les clients débiteurs.
9. **Aucun indicateur de retard** (aucune notion d'échéance), aucun champ `due_date`,
   aucun audit de consultation. La modification de la limite reste auditée par
   `customer.updated` (avant / après).

## Conséquences

- Réutilisable par la caisse et le POS : `SaleService` (validation + encaissement immédiat),
  `PaymentService`, `ReceivableService` ; un paiement `CASH` pourra alimenter la caisse sans
  changer la définition d'une créance.
- Une entreprise créée avant la Phase 2.8 active le module Créances elle-même (Organisation ▸
  Modules) ; les nouvelles l'ont par défaut (profils et plans).
- La validation d'une vente d'un client à limite configurée prend un verrou supplémentaire
  (ligne client), bref.

## Alternatives écartées

- **Table `receivables` / solde stocké sur le client** : seconde source de vérité à maintenir à
  chaque validation, paiement, annulation.
- **Contrôle à la création du brouillon** : aucune exposition n'existe encore ; un brouillon
  peut ne jamais être validé.
- **`credit_limit` nul = crédit interdit** : aurait bloqué toutes les ventes non payées des
  clients existants (aucune limite saisie jusqu'ici).
- **Verrou consultatif (`pg_advisory_xact_lock`)** : équivalent, mais le verrou de ligne sérialise
  aussi une modification concurrente de la limite.
- **Routes client dans le module `customers`** : dépendance `customers → sales` (cycle).
