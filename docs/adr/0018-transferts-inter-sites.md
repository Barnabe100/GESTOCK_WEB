# ADR-0018 — Transferts inter-sites : atomicité, CMUP, annulation, sites et fonctionnalité de plan

- **Statut** : Proposée
- **Date** : 2026-09-24

## Contexte

La Phase 2.5 réalise les transferts de stock entre deux sites d'une même entreprise
(fonctionnalité de plan `stock.transfers`, incluse dans ENTREPRISE, déclarée depuis la 2.2).
Un transfert modifie **deux** sites dans une même opération, ce que le moteur de stock ne
faisait pas : `StockService.apply` travaillait sur un seul site. Il fallait aussi trancher le
coût d'entrée sur le site destination, l'annulation, le périmètre des sites d'un membre et la
façon dont une fonctionnalité de plan conditionne des permissions.

## Décision

1. **Un seul moteur, plusieurs sites, une transaction.** `StockService` gagne
   `apply_many` (mouvements sur plusieurs sites) et `transfer` (paire sortie / entrée). Les
   niveaux (site, article) concernés sont créés au besoin puis **verrouillés dans un ordre
   global (site, article)** : deux transferts croisés A → B et B → A, ou un transfert et une
   vente sur le même article, se sérialisent sans interblocage. **Tout** le stock est contrôlé
   avant la moindre écriture : aucune sortie sans son entrée, aucun état intermédiaire
   visible. Le module Transferts n'a aucun mécanisme de verrouillage propre.
2. **Mouvements** : types réservés depuis la 2.2 — `TRANSFER_OUT` (−q, site source) et
   `TRANSFER_IN` (+q, site destination), `source_type = "stock_transfer"`, `source_number =
   TRF-…` (le journal retrouve le transfert). Pas de nouveau type.
3. **CMUP** : sortie au **CMUP du site source** (Q1), inchangé ; entrée sur le site
   destination **au même coût**, qui recalcule le CMUP destination par la formule STK-05 :
   un transfert entrant est une ENTRÉE pour le site qui reçoit (`COST_ENTRY_TYPES` =
   `ENTRY`, `TRANSFER_IN`). La valeur totale du stock de l'entreprise est conservée (aux
   arrondis près). Coût et valeur sont figés sur les lignes à la validation.
4. **Annulation** (permission `stock.transfer.cancel`, Administrateur par défaut) : un
   brouillon est abandonné sans effet ; un transfert validé est annulé par des mouvements
   inverses `CANCELLATION` — retrait du site destination, remise sur le site source — au coût
   du transfert, reliés aux mouvements d'origine, **CMUP inchangés** (STK-06), dans une
   transaction ; refus total (`insufficient_stock`) si le stock destination ne suffit plus.
   La garde anti double application devient unique par **(ligne source, type, site)**
   (migration `0008`) : une annulation de transfert inverse un mouvement sur chaque site.
5. **Sites** : source ≠ destination (service + `CHECK` en base) ; les deux sites doivent être
   **actifs et accessibles au membre** (`site_access_denied` sinon ; FK composites : jamais un
   site d'un autre tenant) ; le site sélectionné (`X-Site-Id`), s'il y en a un, est l'un des
   deux (`site_mismatch`) ; la permission de l'opération est exigée **sur les deux sites**
   (`site_permission_denied`) — un rôle limité au site A ne suffit pas pour alimenter ou vider
   le site B. Un transfert touchant un site inaccessible est introuvable (`404`).
6. **Fonctionnalité de plan → permissions** : `PermissionDef` accepte `feature=` ; une telle
   permission n'est accordée (capacités), proposée à l'édition des rôles, ni attribuable que
   si le plan inclut la fonctionnalité (`ModuleRegistry.available_permissions`). Les routes
   exigent en plus `require_feature("stock.transfers")` (`403 feature_unavailable`) ;
   l'interface filtre menus et routes par fonctionnalité **et** permission. Aucun test sur le
   nom d'un plan.
7. **Pas d'état « en transit »** : la réception est immédiate à la validation (une seule
   transaction). Un transit (expédition puis réception) pourra s'ajouter plus tard sans
   remettre en cause ce modèle (statut intermédiaire, mouvements sur un site de transit).

## Conséquences

- Les autres documents (entrées, sorties, ventes) profitent du même ordre global de
  verrouillage, sans changement de comportement.
- Le détail `insufficient_stock` indique désormais le site (`site_id`) de chaque article.
- Un changement de plan (ENTREPRISE → STANDARD) masque les transferts existants (lecture
  comprise) ; les données et les mouvements sont conservés.
- Le retour arrière de la migration `0008` est destructif (comme `0007`) : il supprime les
  transferts et leurs mouvements, sans recalcul des niveaux — réservé au développement.

## Alternatives écartées

- **Deux appels `apply` successifs (source puis destination)** : ordre de verrouillage
  différent selon le sens du transfert → interblocages entre transferts croisés.
- **Entrée destination au CMUP destination (sans recalcul)** : la valeur quitterait le site A
  sans arriver sur le site B ; stock de l'entreprise mal valorisé.
- **Annulation interdite après validation** : incohérent avec entrées, sorties et ventes ;
  obligerait à un transfert retour manuel sans lien avec l'original.
- **Accès au seul site source suffisant** : un membre limité à un site pourrait déplacer du
  stock vers un site qu'il ne gère pas.
- **Filtrer les permissions au seul frontend** : la sécurité doit rester côté serveur.
