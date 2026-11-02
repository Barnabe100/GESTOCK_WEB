# Suivi technique — anomalies ouvertes et échecs connus

Ce fichier recense les anomalies techniques **non résolues** et les échecs de tests connus. Rien
n'y est masqué : un test n'est jamais affaibli pour obtenir un résultat vert. Chaque entrée
indique son impact, son état et l'échéance de résolution.

## A-1 — E2E `lots-3ha.e2e.ts` : dialogue de validation démonté (intermittent)

- **Statut** : OUVERT — à résoudre **avant la campagne globale de validation sur Ubuntu**
  (décision de l'utilisateur, clôture de R6.5).
- **Constat** (2026-11-01, exécution complète des E2E de R6.5) : le test « lot périmé : vente
  refusée par défaut, puis dérogation explicite tracée » (et, plus rarement, « vente
  back-office : FEFO multi-lots ») échoue de façon intermittente : le dialogue « Valider la
  vente » s'ouvre (la case « Encaisser un paiement maintenant » est visible et cochée), puis son
  menu déroulant n'est plus stable et est **détaché du DOM** ; la capture finale montre la page
  de la vente sans dialogue. Fréquence observée : 1 échec sur 21 exécutions répétées après le
  premier correctif.
- **Analyse faite** :
  1. Course identifiée et corrigée dans le test (`986a1c0`) : le premier enregistrement fait
     passer la page de `/sales/new` à `/sales/{id}` (remontée du formulaire,
     `SaleForm key={sale.id}`) ; valider avant cette navigation ouvrait un dialogue aussitôt
     démonté. Le test attend désormais l'adresse de la vente.
  2. Échec résiduel : cause **non établie**. Pistes : remontée de la page par un rechargement
     des capacités ou du jeton (`/me/capabilities`, `/auth/refresh` appelés pendant le
     parcours), ou rechargement de la vente invalidant le formulaire. Aucune relation établie
     avec R6.5 (le dialogue concerné n'utilise que le moyen « Espèces » à l'ouverture).
- **Règle** : le test n'est ni désactivé, ni assoupli, ni relancé jusqu'au vert.
- **Prochaine étape** : instrumenter le montage / démontage de `SaleForm` et du dialogue
  (trace Playwright avec capture DOM) pour identifier l'élément qui provoque la remontée, puis
  corriger l'application si la remontée n'est pas voulue.

## E-1 à E-5 — échecs liés à l'environnement d'exécution (cloud de développement)

Constatés lors de l'exécution complète des E2E (R6 puis R6.5), **sans lien** avec les lots
livrés ; à vérifier dans l'environnement Ubuntu complet.

| Réf. | Test(s) | Cause | Condition de réussite |
|---|---|---|---|
| E-1 | `console-licenses.e2e.ts` (paiement confirmé → licence signée ; postes) | service de signature non configuré (`503 signing_service_unavailable`) | Signing Service démarré et configuré (`SM_SIGNING_SERVICE_URL`, `SM_SIGNING_CLIENT_SECRET`, `SM_LICENSE_PUBLIC_KEYS_FILE`) |
| E-2 | `renewal.e2e.ts` (renouveler sans perte de jours) | même cause (génération de licence) | idem |
| E-3 | `console*.e2e.ts` (exécution R6, console arrêtée) | console TechNova non démarrée (`502` du proxy `/platform-api`) | `uvicorn app.console.main:app --port 8001` démarrée (fait lors de l'exécution R6.5 : ces tests passent alors) |
| E-4 | `onboarding.e2e.ts` @mobile | limitation de fréquence persistante de l'inscription publique (`429 rate_limited`) épuisée par les tests d'inscription précédents de la même exécution | base de test neuve, ou exécution isolée du projet mobile |
| E-5 | `signup.e2e.ts` @mobile | idem (`429`) | idem |

## R7-1 — supervision de la tâche quotidienne de conservation (exploitation)

- **Statut** : À FAIRE PAR L'EXPLOITATION (hors du code).
- `stockmanager notifications purge` efface désormais AUSSI les téléphones des clients des
  commandes QR finies depuis plus de 30 jours (décision Q13, ADR-0052). Le code, les droits SQL
  (rôle `stockmanager_purge`) et les tests sont livrés ; la **planification** (cron ou minuterie
  systemd, quotidienne) et la **supervision** (alerte sur code de sortie non nul, consultation
  des compteurs affichés) relèvent de l'environnement de production et ne peuvent pas être
  vérifiées depuis le dépôt. Exemple de minuterie et de vérification : [`MISE_A_JOUR_R7.md`](MISE_A_JOUR_R7.md) §6.

## R7-2 — en-têtes de sécurité des pages publiques en production

- **Statut** : À CONFIGURER au reverse proxy de production (aucun serveur web de production dans
  le dépôt).
- La page publique pose sa CSP par balise et `referrer = no-referrer` ; l'API publique renvoie
  `no-store`, `no-referrer`, `nosniff`, `noindex`. En production, servir aussi `/q` et
  `/q/track` avec l'en-tête CSP et `frame-ancestors 'none'` (non applicable par balise), et
  déclarer le proxy dans `SM_TRUSTED_PROXIES` (`ARCHITECTURE.md` §11).

## R7-3 — « Envoyer » sans réaction sur téléphone (HTTP sur le réseau local) — RÉSOLUE

- **Constat** (recette Ubuntu, 2026-11-02) : page publique QR ouverte sur un téléphone du même
  Wi-Fi (`http://<IP du serveur>:5173`) : menu et sélection fonctionnent, « Envoyer » ne réagit
  pas au toucher.
- **Cause** : `crypto.randomUUID()` (clé d'idempotence) n'existe que dans un contexte
  **sécurisé** (HTTPS ou `localhost`). Par l'adresse IP en HTTP, l'appel levait
  `TypeError: crypto.randomUUID is not a function` avant tout changement d'état : aucune
  requête, aucun message. Reproduit sur ordinateur ET mobile par l'adresse IP ; invisible par
  `localhost` (E2E et développement). Même défaut latent dans le POS, l'encaissement, la caisse,
  l'abonnement et la saisie des commandes. Aucune superposition, `pointer-events` ni `z-index`
  en cause.
- **Correction** : générateur commun `frontend/src/shared/lib/uuid.ts` (`newUuid` :
  `randomUUID` si disponible, sinon `crypto.getRandomValues`, même aléa cryptographique ;
  jamais `Math.random`), test statique interdisant tout appel direct ; page publique : erreur
  imprévue affichée, raison écrite quand un champ obligatoire désactive « Envoyer ». Aucune règle
  de commande ni permission modifiée ; le serveur revérifie tout.
- **Tests** : Vitest (générateur, test statique, envoi sans `randomUUID`), E2E ordinateur et
  mobile (page privée de `randomUUID`), rejoués aussi par l'adresse IP en HTTP.

## Historique

| Date | Entrée | Évènement |
|---|---|---|
| 2026-11-01 | A-1 | ouverte (R6.5) ; course `/sales/new` → `/sales/{id}` corrigée dans le test (`986a1c0`) ; échec résiduel 1/21 |
| 2026-11-01 | E-1 à E-5 | consignées (exécutions complètes R6 et R6.5) |
| 2026-11-01 | R7-1, R7-2 | consignées (livraison R7 : actions d'exploitation) |
| 2026-11-01 | A-1, E-1 à E-5 | exécution complète R7 : E-1 (×2), E-2, E-4, E-5 reproduites (mêmes causes) ; A-1 non reproduite (reste ouverte) |
| 2026-11-02 | R7-3 | ouverte (recette Ubuntu) et résolue : clé d'idempotence sans `crypto.randomUUID` hors contexte sécurisé |
