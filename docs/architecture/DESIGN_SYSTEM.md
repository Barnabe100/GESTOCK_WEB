# Design System — StockManager Web

Référence de l'interface (Phase 2.5-B). Tout nouvel écran suit ce document ; un écart doit
être justifié (et, s'il est durable, intégré ici).

Base technique : **PrimeReact 10 (MIT), thème `lara-light-blue`** — identité existante
conservée ([ADR-0005](../adr/0005-versions-frontend.md)). Pas de Tailwind, pas d'autre
bibliothèque d'interface, pas d'animation décorative ni de graphique superflu.

## 1. Principes

1. **Lisible avant d'être décoratif** : outil de gestion quotidien, densité maîtrisée,
   hiérarchie claire (titre → description → filtres → contenu → actions).
2. **Un même concept s'affiche toujours de la même façon** : statut, montant, date, action
   destructive, état vide.
3. **Piloté par les capacités** : ce qui est affiché dépend de `can(permission)`,
   `hasModule(code)` et `capabilities.features` — **jamais** d'un nom de plan, de rôle ou
   d'un secteur d'activité. Le frontend masque pour l'ergonomie ; le backend reste la seule
   frontière de sécurité.
4. **Ne jamais cacher silencieusement une restriction d'abonnement** : afficher la
   consultation seule (bandeau / message) plutôt que de faire disparaître l'écran.
5. **Tout texte passe par i18n** (`t(...)`), vocabulaire métier via `terminology`.
6. **Réutiliser avant de créer** : un composant partagé n'existe que s'il sert à plusieurs
   écrans.

## 2. Jetons (`frontend/src/styles.css`, `:root`)

| Famille | Jetons | Remarque |
| --- | --- | --- |
| Couleurs | `--sm-primary`, `--sm-text`, `--sm-text-muted`, `--sm-surface`, `--sm-surface-ground`, `--sm-surface-subtle`, `--sm-surface-hover`, `--sm-border`, `--sm-highlight-*` | Dérivées des variables du thème PrimeReact (repli explicite). |
| Tonalités de statut | `--sm-{neutral,info,success,warning,danger}-{bg,fg}` | Fond doux + texte foncé ; contraste 5,7 à 6,8 : 1 (AA). |
| Typographie | `--sm-font`, `--sm-text-{xs,sm,md,lg,xl}`, `--sm-weight-{medium,bold}` | Chiffres tabulaires pour les nombres (`.sm-num`). |
| Espacements | `--sm-space-1` … `--sm-space-6` (0,25 → 2 rem), `--sm-gap` | Échelle unique. |
| Formes | `--sm-radius-{sm,,lg,pill}`, `--sm-shadow-{sm,,lg}` | |
| Focus | `--sm-focus-ring` | Anneau visible au clavier (`:focus-visible`). |
| Densité | `--sm-control-padding-{x,y}` | Contrôles PrimeReact compacts. |
| Coquille | `--sm-sidebar-width`, `--sm-topbar-height`, `--sm-content-max` | |

Règle : **aucune valeur visuelle en dur** dans un composant ; ajouter un jeton si besoin.

## 3. Composants partagés (`frontend/src/shared/ui/`)

| Composant | Rôle |
| --- | --- |
| `AppLayout` (`layouts/`) | Coquille : barre latérale groupée, barre supérieure (site, utilisateur), lien d'évitement, tiroir mobile, **un seul** `<ConfirmDialog />`. |
| `PageHeader` | Titre (h1), `description`, `breadcrumbs` (fil d'Ariane des fiches), `actions` principales. |
| `FilterBar` | `[Recherche] [Filtres] [Réinitialiser]` (`role="search"`, réinitialisation désactivée sans filtre actif). `DateRangeFilter` : période avec libellés visibles « Du / Au ». |
| `SearchInput`, `StatusFilter` | Recherche serveur (anti-rebond) ; filtre Actifs / Inactifs / Tous. |
| `ServerTable` | Tableau paginé côté serveur : tri, pagination (10/25/50/100), survol, rapport « 1–25 sur 120 », défilement horizontal, erreur traduite, état vide. |
| `RowActions` | Actions de ligne : boutons icône (infobulle + `aria-label`) jusqu'à 3, au-delà menu « Plus d'actions » ; action destructive en rouge. |
| `StatusBadge` et dérivés | `DocumentStatusBadge`, `ActiveBadge`, `SubscriptionStatusBadge` — voir §4. |
| `EmptyState`, `ListEmpty` | État vide avec message et action ; `ListEmpty` distingue « aucune donnée » (action de création) de « aucun résultat » (piste : réinitialiser). |
| `LoadingState` | Chargement annoncé (`role="status"`). |
| `ErrorMessage` | Erreur de chargement traduite (`role="alert"`), bouton « Réessayer » ; jamais d'erreur technique brute. |
| `FormField`, `FormSection` | Libellé associé, marque obligatoire (`*` + « (obligatoire) » lu par les lecteurs d'écran), aide, erreur (`role="alert"`) ; regroupement en `fieldset`. |
| `confirmAction` | Confirmation standard des actions sensibles (validation, annulation, désactivation). `danger` → bouton rouge, icône d'alerte, focus par défaut sur « Annuler ». |
| `MetricCard` | Indicateur du tableau de bord (valeur, libellé, tonalité, lien éventuel). |
| `NotFound` | Page introuvable avec retour au tableau de bord. |

## 4. Statuts (identiques partout)

| Code | Libellé (fr) | Tonalité |
| --- | --- | --- |
| `DRAFT` | Brouillon | neutral |
| `VALIDATED` | Validé(e) | success |
| `CANCELLED` | Annulé(e) | danger |
| `ACTIVE` | Actif | success |
| `INACTIVE` | Inactif | neutral |
| `LOW` (niveau de stock) | Stock faible | warning |
| `OUT` (niveau de stock) | Rupture | danger |
| `OK` / `NOT_STOCKED` | Normal / Non stocké | success / neutral |
| Marqueurs (rôle de base, propriétaire…) | — | info |
| Avertissements (protégé, mot de passe à changer) | — | warning |

Le libellé vient de l'espace i18n du module (`sales.statuses`, `stock.documentStatus`) ; la
**tonalité**, elle, est centrale (`DOCUMENT_TONES`, `STATE_TONES`). Ne jamais utiliser
`primereact/tag` directement pour un statut.

## 5. Formulaires

- Dialogue pour les fiches simples (`sm-dialog`, `sm-dialog-wide`) ; page pour les documents
  (entrées, sorties, transferts, ventes) et les réglages (`sm-form-card`).
- Champs obligatoires marqués (`required` sur `FormField`), cohérents avec le schéma Zod.
- Grille `sm-form-grid` (2 colonnes, 1 sur mobile) ; sections `FormSection`.
- Actions en bas à droite (`sm-dialog-actions` / `sm-form-actions`) : secondaire (texte) puis
  **principale en dernier** ; action destructive `severity="danger"`.
- Validation côté client pour l'ergonomie seulement ; les erreurs serveur (`code` stable) sont
  traduites via `errors.json`.

## 6. Tableaux

- `ServerTable` pour toute liste serveur ; `DataTable` + classe `sm-table` pour une petite
  liste locale (rôles, sites, membres).
- Nombres alignés à droite (`headerClassName`/`bodyClassName="sm-num"`), formatés par
  `formatMoney` (XOF sans décimales), `formatQuantity`, `formatCost` ; dates par
  `formatDate` / `formatDateTime` ; numéros de document tels que le serveur les renvoie.
- Colonne « Actions » en dernier (`RowActions`). Lignes de documents cliquables **et** action
  « Ouvrir » accessible au clavier.
- `minWidth` du tableau : au-delà, défilement horizontal dans le cadre (jamais de la page).

## 7. Retours et états d'écran

| Situation | Traitement |
| --- | --- |
| Chargement | `LoadingState` (écran) ou `loading` du tableau. |
| Vide | `EmptyState` / `ListEmpty` avec action de création si permise. |
| Erreur | `ErrorMessage` (traduit) + « Réessayer ». |
| Succès | Toast de succès (`useToast().success`). |
| Échec d'opération | Toast d'erreur traduit (`translateError`, `saleError`, `stockError`). |
| Action désactivée | Bouton `disabled` (jamais un clic sans effet). |
| Restriction d'abonnement | Bandeau « Consultation seule » / message d'information ; l'historique reste consultable. |
| Action sensible | `confirmAction` (désactivation, validation, annulation). |

## 8. Navigation et tableau de bord

- Barre latérale générée depuis le registre des modules, filtrée par permissions et
  fonctionnalités, **groupée** : Tableau de bord · Catalogue · Stock · Ventes et clients ·
  Administration (`NavItem.group`, `NAV_GROUPS`). Un groupe sans entrée visible disparaît.
- Tableau de bord : indicateurs (ruptures, sous le seuil, ventes et transferts en brouillon),
  dernières ventes, actions rapides, abonnement — chaque bloc conditionné par sa permission
  (et la fonctionnalité `stock.transfers` pour « Nouveau transfert »). Aucune requête n'est
  lancée sans la permission correspondante.

## 9. Responsive

| Largeur | Comportement |
| --- | --- |
| > 1024 px (desktop) | Barre latérale fixe, contenu jusqu'à `--sm-content-max`. |
| ≤ 1024 px (tablette) | Barre latérale en tiroir (bouton Menu, voile, Échap), tableau de bord sur une colonne. |
| ≤ 800 px (mobile) | Filtres deux par ligne (recherche et période pleine largeur), lignes de documents empilées, indicateurs deux par ligne, actions rapides avant les listes, pagination simplifiée. |

Garantie testée (Playwright, 390 px) : **aucun débordement horizontal de la page** ; les
tableaux défilent dans leur cadre.

## 10. Accessibilité

- Libellé associé à chaque champ ; `aria-label` sur les boutons icône (plus infobulle).
- Lien « Aller au contenu », focus visible, navigation clavier, `Échap` ferme le menu mobile.
- Dialogues PrimeReact (piège de focus) ; confirmation destructive focalisée sur « Annuler ».
- Erreurs annoncées (`role="alert"`), chargements (`role="status"`), groupes de navigation
  étiquetés (`aria-labelledby`), fil d'Ariane (`nav` + `aria-label`).
- Statuts : texte + couleur (jamais la couleur seule), contraste AA.

## 11. Règles d'usage

- Pas de `if (plan === …)`, `if (role === …)` ni condition sectorielle dans l'interface.
- Pas de texte en dur ; pas de couleur ou d'espacement en dur hors jetons.
- Pas de nouveau composant partagé pour un usage unique ; pas de dépendance d'interface
  supplémentaire.
- Pas de logique métier faisant foi dans React : totaux et contrôles affichés sont indicatifs,
  le backend recalcule.
