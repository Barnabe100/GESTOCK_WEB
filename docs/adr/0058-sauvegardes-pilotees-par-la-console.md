# ADR-0058 — Sauvegardes pilotées par la console TechNova : Sauvegarder, Restaurer, Paramètres

- **Statut** : Acceptée — développée et testée (pytest, Vitest, exercice natif et Docker en CI) ;
  activation sur chaque environnement par l'exploitant
  (`docs/exploitation/ACTIVATION_SAUVEGARDES.md`, sur accord) ; aucune restauration n'a été
  faite sur une base existante
- **Date** : 2026-11-04
- **Prolonge** : ADR-0057 (socle R11-0), ADR-0031 (console TechNova), ADR-0056 (exploitation)
- **Arbitrages appliqués** : D1 (remplacement complet d'UNE entreprise en V1), D2 (abonnements,
  paiements d'abonnement, licences, postes exclus ; journal conservé ; compteurs jamais en
  arrière), D3 (retour arrière 24 h, sauvegarde préalable conservée), D4 (la console ne voit
  que des métadonnées et des agrégats), D5 (deux administrateurs distincts) ; D6 (S3, verrou
  d'objets, chiffrement, clé hors serveur) reste celui de la copie externe de R11-0, préparée

## Contexte

R11-0 a livré le socle (archivage vérifié, sauvegardes physiques, vérification, test réel de
restauration, PITR, supervision) piloté par des minuteries systemd fixes ; la console ne faisait
que lire des statuts. Demande : trois actions intégrées au Design System — **Sauvegarder**,
**Restaurer**, **Paramètres des sauvegardes** — réellement opérationnelles, multi-entreprises,
sans jamais restaurer directement sur la base active pendant les tests, et en conservant toutes
les garanties de R11.

### Ce qui est repris du Desktop (GESTOK_ENTREP, lecture seule)

Le service `app/services/backups` du Desktop (branche de reconstruction) a été lu :
`backup_service.py`, `backup_engine.py`, `postgresql_backup_engine.py`, `backups_page.py`,
`backup_cli.py`. Principes **repris** : un service d'orchestration distinct du moteur
(permissions, configuration, audit, appel du moteur) ; configuration persistante
(`backup.auto_enabled`, `backup.frequency`, `backup.time`, rétention) ; « exécuter si dû »
plutôt qu'une boucle en mémoire ; vérification d'une sauvegarde avant de la proposer ;
**sauvegarde de sécurité avant toute restauration** ; rotation ; journal d'audit de chaque
action ; libellés et page à trois blocs (créer, restaurer, paramètres). Principes **non
transposés** : le Desktop restaure en remplaçant TOUTE la base locale (`pg_restore --clean`,
fichier SQLite copié) par l'utilisateur lui-même — inacceptable en SaaS multi-entreprises ;
le planificateur du Desktop vit dans le processus de l'application (perdu à l'arrêt, une
instance par poste) ; la configuration est une clé texte sans validation de fuseau ; aucune
double validation, aucune copie isolée, aucune notion d'entreprise.

## Décisions

1. **La console décide, l'agent exécute.** La console (rôle `stockmanager_platform`, sans accès
   aux données des entreprises) n'écrit que des paramètres, des demandes et des décisions. Un
   **agent** (`ops/backup/sm-backup-agent.sh`, conteneur de la base, utilisateur postgres,
   socket locale, rôle de réplication `stockmanager_backup`) est lancé chaque minute par la
   minuterie `stockmanager-backup-agent.timer` ; un verrou non bloquant garantit **un seul
   agent** (une minute occupée est sautée). Les minuteries fixes hebdomadaire / quotidienne /
   mensuelle de R11-0 sont **supprimées** : la planification vient de la base. La supervision
   (`health`, toutes les 5 min) et la copie externe restent des minuteries.

2. **Paramètres en base, effet réel** (`backup_policies`, migration 0063) : une ligne par
   catégorie (`physical`, `logical`, `verification`, `restore_test`) et fréquence (`daily`,
   `weekly`, `monthly`, `automatic` = toutes les N heures depuis une ancre, `manual` =
   conservation des sauvegardes à la demande, jamais planifiée) ; heure, jour de semaine (ISO),
   jour du mois (31 → dernier jour), date de début, fuseau IANA (contrôlé), activation,
   conservation ; contraintes en base par fréquence. La **prochaine exécution** est calculée en
   SQL (`backup_policy_next_run`) : la console l'affiche, l'agent la consomme
   (`backup_enqueue_due`) — une seule source de vérité. Une échéance manquée pendant un arrêt
   n'est exécutée **qu'une fois** ; une occurrence = une exécution (unicité
   `(policy_id, scheduled_for)`). Valeurs par défaut identiques à R11-0 (physique hebdomadaire
   dimanche 01:15, vérification quotidienne 02:15, test le 1er à 03:30, UTC) plus la logique
   quotidienne 02:45. Rétention physique = la plus longue des politiques physiques actives (ou
   de la manuelle). Chaque modification : raison obligatoire, audit avant / après.

3. **Physique ≠ logique.** Physique : cluster complet et WAL, objectif **sinistre** et PITR de la
   plateforme. Logique : **une entreprise**, objectif **erreur d'une entreprise**. Fréquences,
   rétentions et historiques séparés. Une sauvegarde d'une entreprise est toujours logique
   (contrainte en base).

4. **Exécutions** (`backup_runs`) : file d'attente et historique (jamais supprimés :
   déclencheur) ; une seule exécution active par type, portée et entreprise (index unique
   partiel : **aucune exécution concurrente**) ; réservation `FOR UPDATE SKIP LOCKED`
   (`backup_claim`) ; signe de vie toutes les 30 s ; fin (`backup_finish`) : en échec, nouvelle
   tentative différée de 5 puis 10 minutes, **3 essais** au plus (jamais pour une sauvegarde
   préalable : l'opération elle-même échoue) ; exécution silencieuse depuis 2 h = interrompue
   puis retentée (`backup_reclaim_stale`). Progression (pourcentage, message), résultat, erreur
   et éléments par entreprise visibles dans la console.

5. **Sauvegarde logique d'une entreprise** (`sm-tenant-export.sh`) : rôle
   `stockmanager_tenant_backup` (SELECT seulement, **sous RLS**, contexte `app.tenant_id`), UNE
   transaction `REPEATABLE READ READ ONLY` (instantané cohérent même sous écritures
   concurrentes, aucun verrou bloquant), CSV par table (colonnes explicites, tri par clé
   primaire, gzip), manifeste (révision Alembic, entreprise, lignes et SHA-256 du contenu),
   `SHA256SUMS`, marqueur `.complete`, construction en `.partial` puis renommage. Le
   **périmètre** est celui des **droits** accordés par la migration : toute table d'entreprise
   est soit sauvegardée, soit dans la liste d'exclusion (test). Exclues (D2) : abonnements,
   paiements d'abonnement, licences, postes, journal, notifications d'échéance et leurs
   lectures.

6. **Restauration d'UNE entreprise** (`restore_operations`, machine à états protégée par
   déclencheur : chaque transition est réservée au rôle qui en a le droit, la demande est
   immuable) :
   1. demande par un administrateur A (raison) : sauvegarde logique **réussie et vérifiée** de
      CETTE entreprise et de la **même révision de schéma**, ou instant (PITR) dans la fenêtre
      restaurable (de la plus ancienne sauvegarde physique vérifiée au dernier WAL archivé) ;
      une seule restauration active par entreprise ;
   2. **vérification sur copie isolée** par l'agent (`sm-restore-test.sh`, mêmes 15 contrôles
      que R11-0 + épreuve) : copie de l'état actuel depuis la dernière sauvegarde physique
      vérifiée et les WAL, empreinte de TOUTES les données, application de l'archive à la copie
      (`sm-tenant-apply.sh`), nouvelle empreinte : **autres entreprises et tables de la
      plateforme identiques**, tables conservées de l'entreprise identiques, relecture de
      chaque table restaurée **identique à la sauvegarde** ; pour un PITR, une première copie à
      l'instant demandé exporte l'entreprise (élément `pitr`), qui suit ensuite le même chemin ;
      rapport conservé dans l'opération ;
   3. **approbation par un administrateur B ≠ A** (contrainte en base
      `approved_by <> requested_by`, `403 restore_same_admin`), dans les 24 h (sinon expirée) ;
   4. application par l'agent : **verrou de l'entreprise** (`SELECT … FOR UPDATE` qui attend la
      fin de ses requêtes en cours, chacune tenant `FOR KEY SHARE` ; les suivantes reçoivent
      `503 tenant_restore_in_progress`, le public un refus générique ; **les autres entreprises
      ne sont jamais concernées**), **sauvegarde préalable** de l'état actuel (protégée 24 h),
      application en UNE transaction (`COMMIT` seulement si tout est conforme, sinon
      `ROLLBACK`), déverrouillage, journal de l'entreprise (`backup.tenant_restored`) et de la
      plateforme ;
   5. **retour arrière** dans les 24 h : NOUVELLE restauration depuis la sauvegarde préalable,
      même vérification, même double approbation.

7. **Sûreté de l'application** (`sm-tenant-apply.sh`) — `session_replication_role = replica` :
   - ce qu'il désactive : les **déclencheurs**, donc les clés étrangères (déclencheurs internes)
     et les gardes d'immuabilité ; ce qu'il ne désactive PAS : **RLS**, `CHECK`, `NOT NULL`,
     unicité (index) — prouvé par test (insertion d'une autre entreprise et d'une valeur nulle
     refusées en mode replica) ;
   - **RLS** : rôle `stockmanager_tenant_restore` sans BYPASSRLS ni superutilisateur ; chaque
     `DELETE` et chaque `INSERT … SELECT` est filtré / contrôlé (`WITH CHECK`) par le contexte
     de l'entreprise : une ligne d'une autre entreprise ne peut être ni vue, ni supprimée, ni
     insérée, quel que soit le contenu de l'archive. `COPY FROM` étant refusé par PostgreSQL
     sous RLS, le chargement passe par des tables TEMPORAIRES (privées à la session), puis
     `INSERT … SELECT` soumis à la RLS ; les lignes d'une autre entreprise dans l'archive sont
     refusées avant toute écriture ;
   - **clés étrangères** contrôlées explicitement après chargement, dans les **deux sens** :
     lignes restaurées → parents (comptes, pays, plans… : colonnes référencées seulement), et
     lignes CONSERVÉES (abonnements, notifications rattachés à un site) → lignes restaurées ; un
     parent invisible compte comme orphelin (échec, jamais un succès par défaut) ;
   - **déclencheurs** des tables restaurées relus et figés par un test : gardes de mise à jour
     seulement (aucune ne concerne `DELETE`/`INSERT`), plus le miroir des codes-barres, dont
     l'effet est contenu dans l'instantané et contrôlé ; tout nouveau déclencheur fait échouer
     le test tant qu'il n'est pas examiné ;
   - nombres de lignes par table, invariants métier (`restore-checks.sql`) ; compteurs de
     numérotation fusionnés au maximum (**jamais en arrière**) ; effacements à 30 jours
     réappliqués (téléphones des commandes, notifications des membres : une sauvegarde ancienne
     ne les ressuscite pas ; constantes vérifiées contre le code) ;
   - un succès de rechargement n'est **pas** la preuve : la preuve est l'ensemble de ces
     contrôles, joués sur la copie isolée PUIS dans la transaction de production.

8. **Restauration GLOBALE** : procédure distincte. PITR de toute la plateforme vérifié sur copie
   isolée et approuvé par deux administrateurs dans la console ; **l'application reste
   manuelle** (procédure documentée), jamais exécutée par l'agent ni par la console.

9. **Multi-entreprises** : sauvegarde globale (toutes les entreprises, un élément par
   entreprise) ou individuelle ; historique global ou par entreprise (ses sauvegardes propres
   et les sauvegardes globales qui la contiennent) ; filtres entreprise, date, période, type,
   statut, déclenchement.

10. **Supervision cohérente avec R11-0** : agent vu depuis moins de 3 minutes, sinon CRITIQUE
    (aucune tâche ne s'exécute) ; par catégorie : non planifiée (avertissement), jamais réussie
    ou dernière réussite trop ancienne (CRITIQUE, mêmes seuils : 26 h / 8 j / 35 j selon la
    fréquence la plus serrée), dernière exécution en échec sans nouvelle tentative
    (avertissement) ; statuts de R11-0 (archivage, stockage, copie externe) inchangés.

11. **Rétention logique** : sauvegardes expirées supprimées sous gardes (chemin exact, nom
    conforme), jamais la dernière d'une entreprise, jamais une sauvegarde protégée ou utilisée
    par une restauration active ou un retour arrière possible.

## Conséquences

- Nouvelles tables de la plateforme (aucune donnée d'entreprise), deux rôles SQL
  (`stockmanager_tenant_backup`, `stockmanager_tenant_restore`, créés par
  `sm-backup-role.sh` ou par la migration jouée en superutilisateur, sans mot de passe :
  socket locale du conteneur seulement), une colonne de verrou sur `tenants`, un verrou
  `FOR KEY SHARE` par requête d'entreprise (n'entre en conflit qu'avec le verrou de
  restauration).
- La copie isolée double le coût d'une restauration (deux copies pour un PITR d'entreprise) : le
  prix de la preuve. L'espace requis est contrôlé avant chaque copie (réserve de R11-0).
- Limites V1 : remplacement COMPLET d'une entreprise (D1) ; restauration d'une entreprise
  seulement dans la même révision de schéma ; l'application suppose l'agent actif ; restauration
  globale manuelle ; copie externe toujours non configurée.

## Preuves

- `backend/tests/test_console_backup_admin.py` (35 tests) : planificateur, nouvelles
  tentatives, reprise, unicité, historique non supprimable, sauvegardes immédiates et audit,
  double approbation (API et base), transitions réservées, sources vérifiées, fenêtre PITR,
  refus / annulation / expiration / retour arrière, restauration globale manuelle, verrou 503
  limité à l'entreprise, périmètre et RLS des rôles, déclencheurs relus, constantes.
- `frontend/src/console/backup-actions.test.tsx` : trois actions, progression, erreurs,
  demande, approbation, paramètres.
- `ops/backup/sm-backup-drill.sh` étape 13 (natif et Docker, CI) : échéance consommée par
  l'agent, sauvegarde d'une entreprise depuis la console, vérification isolée, demandeur refusé,
  application PENDANT des écritures d'autres entreprises, retour arrière avec empreinte des
  autres entreprises identique, archive altérée refusée, PITR d'une entreprise, restauration
  globale non appliquée, échec puis nouvelle tentative.
