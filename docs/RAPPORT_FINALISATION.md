# Rapport de finalisation — mission « Finalisation, industrialisation et commercialisation »

Date : 2026-11-02. Branche : `claude/nifty-ramanujan-12j5pp`. Résultats réellement obtenus ; ce
qui n'a pas pu être vérifié est signalé comme tel. Aucune donnée commerciale, aucun secret,
aucun domaine ni aucune clé de production n'a été créé.

## 1. Commits de la mission

| Commit | Contenu |
|---|---|
| `5ce048b` | Signing Service réellement opérationnel (contrôle `signing check`, script de développement, tests sur le vrai service, CI) — E-1 / E-2 résolus sans contournement |
| `44cd9d5` | R9 — remises par permission (ADR-0054, migration 0061) |
| `0c3b067` | R10 — rapports et rentabilité des recettes (ADR-0055, migration 0062) |
| `33dd217` / `65ec165` / `8b5f78c` | Exploitation (ADR-0056) : PITR, exercice mesuré, export par entreprise, console « Sauvegardes », images de production, nginx, supervision, sécurité, guides ; adaptations des tests à la livraison des rapports ; corrections CI |

## 2. R8 (vérification)

Livré avant la mission (recettes, navigation responsive, correctif QR mobile) ; CI verte
(runs 41369761392, 41380682534). Toujours couvert par les suites (section 5).

## 3. Signing Service

État : **opérationnel** en développement, tests et CI, avec de vraies signatures Ed25519 et
des clés éphémères générées hors du dépôt. Mise en place : `scripts/signing-dev.sh start`
(développement), `tests/test_signing_live.py` (tests et CI), `stockmanager signing check`
(contrôle d'exploitation, échec explicite à la moindre anomalie). URL du service désormais
limitée à `http(s)://`.

Prérequis de production (procédure : [`DEPLOIEMENT.md`](DEPLOIEMENT.md) §4) : hôte séparé ; paire
générée sur cet hôte par TechNova (`signing-service-keygen`) ; clé privée montée en lecture
seule (`0600`), jamais dans le dépôt, une image, la CI, le frontend ni les journaux ; secret
HMAC partagé (≥ 32 caractères) ; clé publique ajoutée au trousseau versionné par une
modification relue ; `stockmanager signing check` vert ; copie chiffrée hors ligne de la clé ;
rotation documentée.

## 4. R9 — ventes, point de vente, caisse

Livré : remise globale (montant ou pourcentage, motif obligatoire), montant et total calculés
par le serveur, contrainte `total = sous-total − remise` en base, permission
`sales.sale.discount` (Administrateur seul parmi les rôles de base), audit, export (« Dont
remise »), reçu, interface (vente et point de vente). Contrôles de caisse vérifiés, inchangés.
**À décider** (ADR-0054) : remboursement d'espèces après clôture (W1), retours partiels,
plafond de remise par rôle, ouverture au Gestionnaire.

## 5. Résultats des tests

| Suite | Résultat |
|---|---|
| Backend, suite complète locale (avant adaptation) | 1 618 réussis, 2 échecs — tous deux dus à la livraison des rapports, corrigés ; fichiers concernés relancés : réussis |
| Backend, CI (run 41408102079) | 1 626 réussis, 1 échec : le test existant anti-clé privée a détecté le marqueur littéral écrit dans `ci.yml` — corrigé (`8b5f78c`) |
| Nouveaux tests backend | remises 26, rapports 21, rentabilité des recettes, export par entreprise 3, console sauvegardes 3, URL de signature 1 : réussis |
| Vitest (complet) | 586 / 587 ; l'échec (délai de 5 s d'un dialogue chargé à la demande, sous charge) réussit relancé (56 / 56) |
| E2E (complet, local, Signing Service actif) | 197 réussis + 1 échec (attente obsolète « rapports à venir ») et 3 non exécutés en conséquence — corrigé, fichier relancé : 5 / 5 → **201 / 201** |
| Exercice PITR (local) | RPO **58 s**, RTO **2 s**, toutes vérifications réussies |
| Images de production (CI) | construites et contrôlées : non root, refus de démarrer sans secrets, nginx valide |
| Lint, format, typage (backend, frontend) | propres ; `alembic check` : aucune dérive |

CI finale : voir section 15.

## 6. R10 — tableaux de bord et rapports

Module `reports` (sans table) : CA remises déduites, remises, panier moyen, encaissé, reste dû,
coût réel des sorties, marge et taux, séries jour / semaine / mois ; répartitions (article,
catégorie, site, vendeur, canal, client) ; paiements par moyen ; rentabilité des recettes ;
exports Excel / CSV / PDF audités ; coûts absents sans `cost_view` ; indicateur « Chiffre
d'affaires du mois ». Formules : [`architecture/REPORTS.md`](architecture/REPORTS.md).

## 7. R11 — administration SaaS (console)

Existant vérifié : entreprises (liste, fiche, suspension, réactivation), abonnements par site
(activation manuelle, prolongation, changement de plan), paiements d'abonnement (confirmation,
rejet), licences (génération, révocation, réémission, fichier), postes (libération), offres
(paramètres commerciaux), catalogue, journal, tableau de bord ; **ajouté** : page « Sauvegardes ».
**À décider** : demandes de support (processus), suivi d'usage — l'usage par entreprise relève
des données métier que la console ne lit jamais (ADR-0031) : définir des agrégats techniques
acceptables avant toute implémentation.

## 8. Sauvegarde et restauration

Procédure et résultats : [`exploitation/SAUVEGARDE_RESTAURATION.md`](exploitation/SAUVEGARDE_RESTAURATION.md).
PITR natif PostgreSQL 16 ; `archive_timeout` 5 min (RPO cible 15 min) ; sauvegarde de base
quotidienne vérifiée ; rétention 30 jours ; restauration toujours dans un répertoire neuf ;
restauration par entreprise = extraction d'une copie restaurée (`stockmanager tenant
export`) ; exercice mesuré à chaque commit (CI). RTO mesuré sur une base réduite : à remesurer
sur une copie de taille réelle.

## 9. Sécurité

Audit : [`exploitation/SECURITE.md`](exploitation/SECURITE.md). Dépendances de production : 0
vulnérabilité connue après mise à jour (zod, fast-uri, pillow, dnspython). Outillage de
développement : 16 vulnérabilités (dont 3 élevées), jamais livré en production.

## 10. Performance

Non mesurée à l'échelle d'un parc réel (aucun test de charge). Mesures disponibles : suites
complètes, exercice de restauration. Les rapports reposent sur des agrégations SQL indexées
(ventes par tenant, mouvements par document), pagination serveur, période bornée à deux ans.
**Recommandé** : test de charge avant ouverture large.

## 11. Supervision

`ops/monitoring/sm-healthcheck.sh` (API prête, WAL ≤ 15 min, sauvegarde vérifiée ≤ 26 h,
disque, Signing Service ; codes 0 / 1 / 2) ; tâches planifiées `ops/cron/stockmanager.cron`.
Outil d'alerte et centralisation des journaux : à choisir.

## 12. Procédure de déploiement

[`DEPLOIEMENT.md`](DEPLOIEMENT.md) : architecture, variables et secrets (échec explicite s'ils
manquent), première installation, clés de signature et rotation, sauvegardes, mise à jour,
retour arrière, éléments non fournis.

## 13. Listes de contrôle

[`exploitation/CHECKLISTS.md`](exploitation/CHECKLISTS.md) : « Avant le premier client » et
« Accueil d'un client pilote ».

## 14. Préparation commerciale

Aucun tarif ni engagement créé. Les paramètres commerciaux des plans se saisissent dans la
console (validation TechNova). Les opérateurs de paiement du Burkina Faso restent en attente
de données vérifiées (catalogue livré vide, jamais requis). Aucun paiement en ligne ni
agrégateur.

## 15. CI

Runs de la mission : 41380682534 (Signing Service, vert), 41402013436 (R10 : échec des 2 tests
adaptés depuis), 41408102079 (exploitation : images vertes ; échecs corrigés par `8b5f78c`),
puis le run de `8b5f78c` (résultat : voir le message final de la mission).

## 16. Risques

1. TLS, domaine, hébergeur, gestionnaire de secrets non choisis.
2. Pas de MFA ni de récupération de mot de passe.
3. Copie hors site des sauvegardes non mise en place (outil à choisir).
4. RTO réel inconnu tant que l'exercice n'est pas joué sur une base de taille réelle.
5. Aucun test de charge ni test d'intrusion externe.
6. Outillage de développement vulnérable : ne jamais exposer le serveur Vite sur Internet.
7. Décisions métier en attente (section 17) : comportements actuels conservateurs.

## 17. Décisions à prendre par TechNova

1. Remboursement après clôture (W1), retours partiels, plafond de remise, remise pour le
   Gestionnaire (ADR-0054).
2. Rôles des rapports (ADR-0055 §6).
3. Support et suivi d'usage dans la console (R11).
4. Infrastructure de production (section 16, points 1 à 3).
5. Tarifs, opérateurs de paiement par pays.
6. Activations de modules planifiés héritées (décision ouverte d'E.1, traitée à chaque
   livraison : rapports remis à `false` sur les sites existants par 0062).

## 18. Guides de mise à jour (Ubuntu)

Depuis R8 : `git pull`, `uv sync` (pillow et dnspython mis à jour), `alembic upgrade head`
(0061 remises, 0062 livraison des rapports), `stockmanager catalog sync`, `npm install` ;
`docker-compose.yml` inchangé (port local 5433 conservé), aucune donnée supprimée, aucune
variable nouvelle obligatoire (`SM_BACKUP_DIR` facultatif pour la console). Activer
« Rapports » explicitement par site.

## 19. Ce qui n'a pas pu être vérifié dans le conteneur

Construction locale des images (Docker Hub refuse les téléchargements depuis l'environnement :
429) — construites par la CI à la place ; déploiement réel (aucun accès de production).

## 20. Prochaine étape proposée

Validation fonctionnelle de R9 et R10 sur Ubuntu, décisions de la section 17, puis
préparation de l'environnement de production selon `DEPLOIEMENT.md` et la liste « Avant le
premier client ».
