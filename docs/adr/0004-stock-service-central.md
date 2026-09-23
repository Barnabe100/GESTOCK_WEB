# ADR-0004 — Stock : service central et journal de mouvements

- **Statut** : Proposée
- **Date** : 2026-09-23

## Contexte

Règles héritées du Desktop à conserver : stock jamais négatif, modifications via un
service central, mouvements traçables, vente validée jamais appliquée deux fois,
montants en `Decimal`. En Web, les accès sont concurrents (plusieurs caisses, sites, utilisateurs).

## Décision

- `StockService` est **le seul** code autorisé à modifier le stock.
- Modèle : `stock_levels` (niveau courant par site et article, `CHECK (quantity >= 0)`)
  + `stock_movements` (journal **append-only**, quantité signée, document source, auteur).
- Chaque opération : verrouillage des lignes (`SELECT … FOR UPDATE`, ordre déterministe),
  contrôle de disponibilité, écriture du mouvement et du niveau, **dans la transaction
  du document source** (vente, réception, transfert, inventaire).
- Les documents qui impactent le stock ont une **transition d'état conditionnelle**
  et une **clé d'idempotence** pour garantir une application unique.
- Quantités `NUMERIC(18,3)`, montants `NUMERIC(18,2)`, `Decimal` en Python,
  chaînes dans l'API.

## Conséquences

- Traçabilité complète et reconstruction possible des niveaux depuis le journal.
- Contention possible sur un article très vendu : acceptable à l'échelle visée,
  à surveiller.
- Les extensions (lots, péremption, variantes) s'ajoutent dans ce service sans
  changer ses appelants.

## Alternatives écartées

- **Stock calculé uniquement à la volée depuis les mouvements** : lent et
  complexe à contraindre (non-négativité).
- **Mises à jour directes du niveau depuis chaque module** : perte de traçabilité,
  règles dupliquées.
