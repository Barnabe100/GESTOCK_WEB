# ADR-0057 — Sauvegardes R11-0 : archivage vérifié, rôle de réplication, contrôles, test réel de restauration, supervision

- **Statut** : Acceptée — développée et testée ; activation sur chaque environnement par
  l'exploitant (procédure `docs/exploitation/ACTIVATION_SAUVEGARDES.md`) ; copie externe
  préparée, non opérationnelle tant que TechNova n'a pas choisi le fournisseur
- **Date** : 2026-11-02
- **Prolonge** : ADR-0056 (exploitation), ADR-0031 (console TechNova)
- **Arbitrages** : D1 à D6 de la conception R11 (remplacement complet d'une entreprise en V1,
  exclusions, fenêtre de retour arrière, agrégats seulement dans la console, double validation,
  S3 + verrouillage d'objets + chiffrement, clé maîtresse hors serveur) — R11-0 n'implémente que
  le socle d'exploitation ; les restaurations pilotées depuis la console (R11-A à R11-H) restent
  à valider étape par étape.

## Contexte

L'audit des sauvegardes a relevé : aucune sauvegarde réelle sur l'environnement Ubuntu, aucun
archivage dans le `docker-compose.yml` de développement, des scripts incompatibles avec busybox
(image Alpine), une base inactive faussement signalée en retard, un échec d'archivage traité
comme un simple avertissement, un seuil de 26 h codé en dur pour des sauvegardes de base, aucun
test réel de restauration, aucune copie externe. Contraintes : ne jamais toucher la base active
ni son volume, ne jamais restaurer sur elle, port local 5433 préservé, aucune donnée métier
modifiée, aucun secret dans le dépôt.

## Décisions

1. **Complément Compose** `docker-compose.backup.yml` (jamais une modification de
   `docker-compose.yml`) : arguments d'archivage du service `db`, répertoire de l'hôte
   `SM_BACKUP_HOST_DIR` monté en `/backups`, scripts en lecture seule, arrêt propre (120 s) ;
   la console ne reçoit que `status/` en lecture seule ; service `offsite` (profil, Restic).
   Aucun port, aucun volume ni service existant n'est redéfini.
2. **Archivage atomique et vérifié** (`sm-wal-archive.sh`) : compression gzip (un segment basculé
   par `archive_timeout` passe de 16 Mio à ~16 Kio, mesuré), relecture complète comparée à la
   source, fsync, renommage, fsync du répertoire ; jamais d'écrasement (identique = succès pour la
   reprise, différent = échec) ; refus sans marqueur de racine (montage absent) ou sous la
   réserve d'espace. Restitution (`sm-wal-restore.sh`) : absent = fin normale (1), présent mais
   corrompu = code 126, qui ARRÊTE la restauration (jamais une restauration silencieusement
   incomplète).
3. **Rôle de réplication dédié** `stockmanager_backup` (`sm-backup-role.sh`, idempotent) :
   `LOGIN REPLICATION`, sans superutilisateur, `NOBYPASSRLS`, `NOINHERIT`, 5 connexions ;
   `EXECUTE pg_switch_wal()` (clôturer un segment, aucune donnée écrite) et `SELECT
   alembic_version` seulement. Aucune donnée d'entreprise lisible (prouvé par l'exercice).
   Authentification : socket locale du conteneur ; mot de passe facultatif (fichier 0600, entrée
   standard de psql, journalisation coupée) pour une connexion TCP en `scram-sha-256`.
4. **Sauvegarde complète hebdomadaire** (`sm-base-backup.sh`) : verrou global (une seconde
   exécution est refusée avec le code 75 sans écraser le statut en cours), contrôle d'espace,
   `pg_basebackup` en arrière-plan (interruptible), `pg_verifybackup` obligatoire, métadonnées
   (dates, durée, taille, LSN, timeline, identifiant système, sommes de contrôle des pages),
   lecture seule ; interruption = statut `interrupted` et partiel supprimé ; partiel orphelin
   (arrêt brutal) supprimé sous verrou à l'exécution suivante.
5. **Vérification quotidienne** (`sm-backup-verify.sh`) : `pg_verifybackup` de chaque sauvegarde
   conservée (le résultat la rend inutilisable pour la rétention et la restauration), continuité
   des WAL depuis la plus ancienne sauvegarde vérifiée en suivant les timelines (`.history`),
   cohérence de chaque segment (CRC gzip, taille, en-tête : nombre magique, adresse, timeline,
   identifiant système), éléments de restauration présents. Complète des WAL le dimanche.
6. **Test réel de restauration mensuel** (`sm-restore-test.sh`) : la plus récente sauvegarde
   vérifiée, copie dans `restore-test/<exécution>` (marqueur créé avant la copie), instance
   temporaire sur socket seule, port distinct, archivage coupé, `pg_hba` réduit à la socket ;
   15 contrôles (point de restauration atteint et octets perdus, révision Alembic, isolement de
   la copie, `pg_amcheck --heapallindexed --parent-check`, clés étrangères, CHECK, unicité
   revérifiée SANS index, contraintes validées, invariants métier, RLS activée et forcée, rôle
   applicatif sans contournement, rien sans contexte, rien d'une autre entreprise pour CHAQUE
   entreprise, écriture inter-entreprises refusée, RTO). Suppression sous gardes, échec fermé
   (code 3) au moindre doute, `--keep` et `--cleanup`.
7. **PITR** (`sm-restore-pitr.sh`) : cible heure UTC explicite, LSN, point nommé ou fin de
   sauvegarde ; refus d'un répertoire non vide, d'un lien, de la production, des répertoires
   internes, d'une sauvegarde non vérifiée, d'une chaîne WAL incomplète ou corrompue, d'une cible
   postérieure au dernier WAL archivé.
8. **Rétention** (`sm-backup-prune.sh`) : simulation PAR DÉFAUT (`--apply` pour supprimer),
   30 jours, ancre vérifiée avant la fenêtre, dernière vérifiée jamais supprimée, minimum de 2,
   tous les candidats contrôlés avant toute suppression (un doute = rien supprimé), WAL supprimés
   seulement avant la plus ancienne sauvegarde conservée, historiques jamais supprimés.
9. **Supervision** : mêmes règles en ligne de commande (`sm-backup-health.sh`, codes 0/1/2,
   `status/health.json`) et dans la console (`app/console/backups.py`) : base inactive ≠ retard
   (segments terminés non archivés au-delà de 15 min) ≠ échec non résolu (CRITIQUE) ; seuils
   configurables (8 jours, 26 h, 35 jours, 15 min, disque 15 % / 5 %, opération bloquée 24 h,
   supervision muette 15 min) ; la console ne lit que des statuts (noms fixes, taille bornée,
   liste blanche de champs), jamais le contenu des sauvegardes.
10. **Planification** : unités systemd en UTC explicite (dimanche 01:15, chaque jour 02:15, le
    1er à 03:30, toutes les 5 minutes, copie externe 04:15), `Persistent=true` ; alternative cron
    (hôte en UTC, `flock -n`). La planification des notifications est inchangée.
11. **Copie externe** préparée (Restic, S3-compatible, chiffrement du dépôt, vérification par
    échantillon) : « non configurée » tant que dépôt, clé et identifiants ne sont pas fournis ;
    jamais initialisée automatiquement.
12. **Intégrité** : `data_checksums` est désactivé sur l'instance existante. `pg_amcheck`,
    `pg_verifybackup` et les contrôles de restauration ne REMPLACENT PAS les sommes de contrôle
    des pages ; l'activation (`pg_checksums --enable`, instance arrêtée) est une opération
    distincte, non réalisée.

## Conséquences

Aucune migration (aucune table nouvelle) ; aucun droit ajouté aux rôles applicatif et console ;
rôle `stockmanager_backup` créé à l'activation. Preuves : `sm-backup-drill.sh` (modes `native`
et `docker`, CI), `tests/sm-backup-unit-test.sh` (image Alpine, CI), `tests/test_console_backups.py`,
`frontend/src/console/backups.test.tsx`. Limites : RTO mesuré sur une base réduite, copie
externe et alertes sortantes à configurer, sommes de contrôle des pages désactivées, clé du dépôt
Restic présente sur le serveur pour écrire (atténuations : verrouillage d'objets, clé par serveur
révocable depuis un poste hors ligne).
