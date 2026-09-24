# ADR-0019 — Inventaires : écart sur le stock courant, ajustements via StockService, module `inventory_count`

- **Statut** : Proposée (en attente de validation TechNova)
- **Date** : 2026-09-24

## Contexte

La Phase 2.6 réalise les inventaires physiques. Le comptage dure : pendant ce temps, des
entrées, sorties, ventes et transferts modifient le stock du site. Il fallait fixer la base de
l'écart, la manière d'ajuster le stock sans second moteur, le CMUP des excédents, la
concurrence, l'intégration au registre des modules (module `inventory_count` déclaré `planned`
depuis la Phase 1 et déjà inclus dans les plans) et l'URL de l'API.

## Décision

1. **Écart = quantité physique − stock courant relu et verrouillé à la validation.** Le stock
   théorique initial (relevé au démarrage du comptage) est conservé pour la traçabilité
   seulement ; le stock à la validation est figé sur la ligne avec l'écart et sa valeur.
2. **Un seul moteur de stock.** `InventoryService` n'écrit jamais un niveau : il lit le stock
   courant via `StockService.lock_levels` (ordre global de verrouillage) puis applique les
   écarts non nuls par `StockService.apply`, dans la transaction de la requête. Aucune
   extension de `StockService` n'a été nécessaire.
3. **Mouvements `ADJUSTMENT`** (type réservé en 2.2), signés, `source_type = 'inventory_count'`,
   `source_number = INV-…`. Pas de nouveaux motifs de sortie : le type et la source suffisent à
   identifier l'ajustement.
4. **CMUP** : excédent valorisé au CMUP courant, **sans recalcul** (aucun coût d'acquisition :
   `ADJUSTMENT` n'est pas un type d'entrée à coût) ; manquant au CMUP courant, inchangé.
5. **Cycle de vie** `DRAFT → COUNTING → READY_TO_VALIDATE → VALIDATED`, annulation possible
   avant validation, reprise du comptage depuis `READY_TO_VALIDATE` ; transitions centralisées ;
   validé = immuable. Toutes les lignes doivent être comptées avant la fin du comptage.
6. **Idempotence et concurrence** : verrou de l'inventaire avant toute transition (la seconde
   validation simultanée reçoit 409, sans effet) ; verrous des niveaux dans l'ordre global du
   moteur (inventaire + vente ou transfert simultanés : sérialisés, sans interblocage).
7. **Un article dans un seul inventaire en cours par site** : vérification sérialisée par un
   verrou consultatif de transaction par site (`pg_advisory_xact_lock`) à la création, à la
   modification et au démarrage — évite deux ajustements concurrents du même article.
8. **Inventaire complet** = articles actifs ayant un niveau sur le site ; liste recalée au
   démarrage. **Ciblé** = articles actifs choisis, même jamais gérés sur le site (stock 0).
9. **Module** : implémentation du module déjà déclaré `inventory_count` (permissions
   `inventory_count.inventory.{view,create,update,count,validate,cancel}`, convention
   `module.ressource.action` imposée par le registre). Aucune fonctionnalité de plan : cœur du
   stock, disponible en STANDARD et ENTREPRISE ; politique d'abonnement inchangée.
10. **URL** : `ModuleManifest.route_prefix` (générique, défaut dérivé du code) monte le routeur
    sous `/api/v1/inventories` sans renommer le module (déjà référencé par les plans, profils et
    activations des tenants). Le registre refuse deux modules sur le même préfixe.
11. **Rôles de base** : Gestionnaire reçoit `inventory_count.*` (y compris validation et
    annulation d'un inventaire non validé) ; Vendeur `inventory_count.inventory.view` ;
    Consultant par `*.view` ; Administrateur par `*`.

## Conséquences

- Le stock final d'un article inventorié est exactement la quantité comptée, quels que soient
  les mouvements pendant le comptage ; l'écart affiché pendant le comptage est indicatif, le
  résumé avant validation est calculé sur le stock courant.
- Valider l'inventaire d'un article ciblé jamais géré sur le site crée son niveau (comme toute
  opération de stock).
- Un article compté à 0 dont le CMUP était nul reste à CMUP 0 ; un excédent sur un article à
  CMUP nul est valorisé à 0 (pas de prix saisi par l'utilisateur).
- Migration `0009` : deux tables avec RLS et droits minimaux ; retour arrière destructif
  (comme `0007`/`0008`), réservé au développement.

## Alternatives écartées

- **Écart sur le stock initial** (physique − stock capturé) : faux dès qu'un mouvement a lieu
  pendant le comptage (exemple : 95 − 100 = −5 laisserait 100 au lieu de 95).
- **Gel du stock pendant le comptage** : bloquerait ventes et réceptions ; inutile avec la
  relecture du stock courant.
- **Ajustement « à la cible » dans `StockService`** : possible, mais la lecture verrouillée
  existante (`lock_levels`) + `apply` suffisent, sans élargir l'API du moteur.
- **Motifs `INVENTORY_SURPLUS` / `INVENTORY_SHORTAGE`** : redondants avec le type `ADJUSTMENT`
  et la source ; auraient demandé des données système supplémentaires.
- **Renommer le module en `inventories`** : impose une migration des données (plans, profils,
  activations) pour un gain purement cosmétique ; le préfixe d'URL suffit.
- **Recalcul du CMUP sur les excédents** : exigerait un prix saisi, contraire à la règle.
