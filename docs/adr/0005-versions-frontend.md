# ADR-0005 — Versions frontend : PrimeReact 10 (MIT), TypeScript 6.0

- **Statut** : Acceptée (validée le 2026-09-23 : PrimeReact 10.9 MIT conservé)
- **Date** : 2026-09-23

## Contexte

- **PrimeReact 11** (dernière version) est devenu une bibliothèque de primitives
  *non stylées* distribuée sous la **licence commerciale PrimeUI** : clé de licence
  requise ; licence « Community » gratuite uniquement sous conditions
  (< 1 M USD de CA annuel, < 5 développeurs, < 10 employés, < 3 M USD de financement,
  renouvellement annuel) ; sinon licence payante par développeur.
- **PrimeReact 10.9.x** (branche `v10-stable`) reste sous **licence MIT**, avec
  composants stylés et thèmes prêts à l'emploi, compatible React 19.
- **TypeScript 7** (compilateur natif) n'est pas encore supporté par
  `typescript-eslint` (qui exige `< 6.1`).

## Décision

- Utiliser **PrimeReact `~10.9.9`** et **PrimeIcons `^7`** (MIT) ; thème Lara.
- Utiliser **TypeScript `~6.0`** pour garder ESLint typé opérationnel.
- Réévaluer ces deux choix à chaque phase (montée de version planifiée).

## Conséquences

- Aucune contrainte de licence ni de clé pour démarrer.
- La v10 recevra moins d'évolutions ; une migration vers la v11 (ou une autre
  bibliothèque) sera un chantier à planifier. Pour la limiter, les écrans métier
  utilisent des **composants partagés** (`src/shared/ui`) qui encapsulent PrimeReact
  lorsque c'est utile.

## Alternatives écartées (à ce stade)

- **PrimeReact 11** : possible si TechNova accepte la licence PrimeUI (Community ou
  commerciale) ; implique aussi de construire tout le style (primitives non stylées).
- **TypeScript 7** : à adopter dès que l'outillage lint le supporte.
