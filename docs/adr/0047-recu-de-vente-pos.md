# ADR-0047 — Reçu de vente 80 mm, monnaie rendue explicite et erreur POS obsolète

- **Statut** : Acceptée (palier POS — encaissement, monnaie rendue et reçu)
- **Date** : 2026-10-07
- **Prolonge** : [ADR-0020](0020-paiements-des-ventes.md) (paiements, monnaie calculée par le
  serveur), [ADR-0023](0023-point-de-vente.md) (point de vente),
  [ADR-0027](0027-identite-documentaire.md) (identité documentaire du tenant),
  [ADR-0037](0037-encaissement.md) (moyens de paiement, espèces, crédit)

## Contexte

La recette du POS Web a relevé trois manques :

1. la monnaie à rendre n'était pas explicite (« Espèces reçues … — monnaie rendue » sur une
   seule ligne, aucun résumé avant validation) ;
2. une erreur d'encaissement restait affichée après la correction du panier : l'état d'erreur
   n'était effacé qu'au lancement d'une nouvelle tentative, jamais quand le panier, le client
   ou les paiements changeaient ;
3. aucun reçu imprimable (le dialogue après vente n'était qu'un point d'extension).

## Décision

### Monnaie rendue

- Le serveur reste la source de vérité : monnaie (`change_given`) calculée sur la seule partie
  espèces à l'encaissement (inchangé). L'interface affiche un résumé **indicatif** (`cashSummary`)
  : Total, Montant reçu, Reste dû, **« Monnaie rendue »** (libellé exact), dans le dialogue de
  paiement, la confirmation et le panier ; jamais un reste dû et une monnaie rendue positifs
  ensemble. Après la vente, seules les valeurs renvoyées par le serveur sont affichées.

### Erreur POS

- Le résultat d'une tentative d'encaissement (erreur, dérogations proposées) ne vaut que pour
  son panier : il est effacé à toute modification du panier, du client ou des paiements, et à la
  réussite. Aucune autre logique modifiée (idempotence, contrôles serveur).

### Reçu

- **Construit par le serveur à partir de la vente persistée** (jamais du panier) :
  `GET /sales/{id}/receipt` — identité documentaire du tenant, site, numéro, date, caissier,
  client, lignes telles que vendues (présentation et prix figés, « 2 Carton 24 × 10 500 »),
  total, paiements effectués, montant reçu, monnaie rendue, reste dû, crédit, nombre
  d'impressions. Aucune donnée interne (coûts, CMUP, lots, stock, audit, permissions). Détail
  des paiements seulement avec `sales.payment.view` sur le site. Vente validée seulement
  (`409 receipt_unavailable`).
- **Impression** : `POST /sales/{id}/receipt/print` autorise, journalise (`sale.receipt_printed`,
  numéro d'impression, réimpression ou non) et renvoie le reçu à imprimer ; verrou de la vente
  (numérotation sans course). Le journal d'audit fait foi pour distinguer impression et
  réimpression : **aucune migration**.
- **Permissions** (nouvelles, nature `read`) : `sales.sale.receipt_print` (première
  impression) et `sales.sale.reprint` (impressions suivantes) ; Administrateur, Gestionnaire et
  Vendeur (un vendeur réimprime lui-même un reçu non imprimé à l'encaissement : panne
  d'imprimante, interruption), pas le Consultant. Consultation : `sales.sale.view` et sa portée
  (ses ventes, ou toutes avec `view_all`) ; impression et réimpression : même portée.
- **Format V1 : ticket thermique 80 mm**, impression du navigateur (aucune dépendance à une
  imprimante) : le reçu est rendu dans une racine d'impression dédiée, seule imprimée, et la
  règle `@page { size: 80mm auto; margin: 0 }` n'est injectée que le temps de l'impression.
  Le contenu (`SaleReceipt`) ne dépend pas du format ; un format (`RECEIPT_FORMATS`) = une
  largeur, une classe CSS et une règle `@page`. 58 mm et A4 : simples entrées futures,
  **non implémentées**, aucune interface de choix en V1.
- **Logo du tenant** (`logo_url` de l'identité documentaire, ADR-0027) : en haut du reçu, avant
  les informations de vente, nom de l'entreprise conservé ; aucun logo = aucune image. L'impression
  attend le chargement des images du reçu (chargées ou en erreur, au plus 3 s) : un logo tout juste
  inséré n'est pas encore chargé et manquerait au ticket ; une image lente ou cassée ne bloque
  jamais l'impression.
- **Interface** : après la vente au POS, « Voir le reçu » et « Imprimer » ; sur la fiche d'une
  vente validée (historique), « Voir le reçu » et « Imprimer » / « Réimprimer » selon les
  impressions déjà journalisées et les permissions (le serveur refait le contrôle).

## Conséquences

- Le reçu reste cohérent après la réinitialisation du panier : il est relu depuis la vente.
- Chaque impression est traçable dans la chronologie de la vente.
- Ajouter un format = une entrée et ses règles CSS, sans toucher au contenu ni au flux.

## Alternatives écartées

- **Reçu construit côté client à partir du panier** : divergence possible avec la vente
  enregistrée, informations non contrôlées par le serveur.
- **Colonne « imprimé » sur la vente** : migration pour une information que le journal d'audit
  porte déjà.
- **Pilote d'imprimante thermique (ESC/POS)** : dépendance matérielle hors périmètre V1.
