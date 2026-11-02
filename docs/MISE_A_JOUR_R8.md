# Mise à jour vers R8 — recettes, navigation responsive, correctif QR mobile (Ubuntu)

Guide de mise à jour d'une installation existante (développement ou recette sur Ubuntu) vers le
palier R8 ([ADR-0053](adr/0053-recettes.md)). **Aucune donnée n'est supprimée** : deux migrations
additives (0059, 0060), aucune réinitialisation. Prérequis : installation à jour en R7
(migration 0058, voir [`MISE_A_JOUR_R7.md`](MISE_A_JOUR_R7.md)).

## Règles de sécurité de la mise à jour

- Ne **jamais** exécuter `docker compose down -v`, `dropdb`, ni supprimer le volume PostgreSQL.
- `docker-compose.yml` n'est **pas modifié** par R8 (dernière modification : R4) : la
  modification locale du port PostgreSQL `5433` est conservée par `git pull`.
- Les fichiers `.env` ne sont jamais versionnés : les vôtres restent en place. Aucune nouvelle
  variable d'environnement n'est requise par R8.

## 1. Sauvegarde (recommandée)

```bash
pg_dump -h localhost -p 5433 -U stockmanager -Fc stockmanager > ~/stockmanager-avant-r8.dump
```

## 2. Code, dépendances, migrations

```bash
cd ~/GESTOCK_WEB
git status                          # vos modifications locales apparaissent (docker-compose.yml…)
git pull origin claude/nifty-ramanujan-12j5pp
git diff docker-compose.yml         # le port 5433 est toujours là

docker compose up -d db             # base existante, volume conservé
cd backend
uv sync                             # aucune nouvelle dépendance
uv run alembic current              # attendu avant : 0058
uv run alembic upgrade head         # 0059 recettes, 0060 livraison
uv run alembic current              # attendu : 0060 (head)
uv run stockmanager catalog sync    # aucune offre modifiée (recettes déjà dans STANDARD et ENTREPRISE)
cd ../frontend && npm install
```

Avec Docker Compose pour le backend : `docker compose up -d --build` (le service `migrate`
applique les migrations et le catalogue à partir de l'image reconstruite).

## 3. Activation des recettes (par site)

Le module est livré **désactivé sur tous les sites existants** (migration 0060). Pour un site :

1. Page « Modules » du site : activer **Menu** (s'il ne l'est pas), puis **Recettes**.
2. Catalogue : le **plat** est un article **non géré en stock** (case « Géré en stock »
   décochée) avec son prix de vente ; les **ingrédients** sont des articles **gérés en stock**,
   dans l'assortiment du site, reçus au coût (CMUP).
3. Restauration → Recettes → « Nouvelle recette » : plat, présentation (unité de base ou
   conditionnement), rendement (portions produites), ingrédients en unité de base.

À partir de là, toute vente validée du plat sur ce site (vente, point de vente, règlement d'une
commande) sort ses ingrédients du stock ; une annulation les restaure.

## 4. Scénarios de validation fonctionnelle

1. **Fiche** : créer une recette (rendement 4 ; 1 kg de riz, 4 œufs) ; onglets Composition,
   Coût de revient (coût par portion et marge, réservés à la permission des coûts), Disponibilité
   (portions réalisables, ingrédient limitant), Versions.
2. **Règles** : plat géré en stock refusé ; ingrédient non géré refusé ; 4 œufs pour 3 portions
   refusé (quantité entière par portion) ; une seule recette par présentation.
3. **Vente** : vendre 2 plats → riz −0,5 kg, œufs −2 ; journal des mouvements : une sortie par
   ingrédient, source = la vente ; CMUP inchangé ; revalider : impossible.
4. **Annulation** : annuler la vente (paiements annulés d'abord) → ingrédients restaurés.
5. **Manque** : vendre plus que le stock d'œufs ne permet → « Ingrédients indisponibles pour
   préparer ce plat (…) », rien n'est écrit.
6. **Menu et commandes** : sans œufs, l'élément du menu est « non commandable » (motif
   « ingrédients insuffisants ») ; commande refusée ; page QR publique : « Épuisé ».
7. **Cuisine** : commande du plat → bouton « Fiche technique » sur la ligne → quantités mises à
   l'échelle, instructions, aucun coût.
8. **Garde** : rendre le plat « géré en stock » → refusé (recette active).
9. **Navigation** : sur ordinateur (fenêtre basse), tablette et téléphone (portrait et
   paysage), ouvrir chaque rubrique : toutes les entrées sont visibles ou atteignables par
   défilement, aucune coupée.
10. **QR mobile** (correctif R7-3) : depuis un téléphone sur `http://<IP du serveur>:5173`,
    commander et toucher « Envoyer » → page de suivi.

## Retour arrière

La descente 0060 → 0059 est sans effet ; 0059 → 0058 est refusée dès qu'une recette existe
(protection des données). En cas de problème : restaurer la sauvegarde de l'étape 1
(`pg_restore`) **dans une nouvelle base**, jamais en supprimant l'existante sans sauvegarde.
