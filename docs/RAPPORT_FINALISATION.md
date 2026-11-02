# Rapport de finalisation — mission « Finalisation, industrialisation et commercialisation »

Date : 2026-11-02. Branche : `claude/nifty-ramanujan-12j5pp`. Ce rapport décrit ce qui a été
livré, vérifié, et ce qui reste à décider par TechNova. Les résultats de tests sont ceux
réellement obtenus (section 5).

## 1. État de départ vérifié

- R8 (recettes, navigation responsive, correctif QR mobile) livré avant la mission, CI verte.
- Signing Service : E-1 / E-2 résolus sans contournement (vrai service, clés éphémères hors
  dépôt) — commit `5ce048b`, CI verte (run 41380682534).

## 2. Livré pendant la mission

| Lot | Commit | Contenu |
|---|---|---|
| Licences opérationnelles | `5ce048b` | `stockmanager signing check`, `scripts/signing-dev.sh`, tests sur le vrai service, CI |
| R9 — remises | `44cd9d5` | remise globale par permission (`sales.sale.discount`, ADR-0054, migration 0061) |
| R10 — rapports | `0c3b067` | module `reports` (ADR-0055, migration 0062), rentabilité des recettes, tableau de bord |
| Exploitation | (ce lot) | PITR et exercice mesuré, export par entreprise, page console « Sauvegardes », images de production, nginx, supervision, audit de sécurité, guides (ADR-0056) |

## 3. Décisions prises et décisions laissées à TechNova

Prises (réversibles, documentées) : Administrateur seul détenteur de `sales.sale.discount` ;
Gestionnaire `reports.*`, Consultant en consultation ; activation EXPLICITE des rapports sur
les sites existants ; restauration par entreprise = extraction (jamais en place).

À décider (aucune implémentation sans accord) :
1. Remboursement d'espèces après clôture de la session (W1), retours partiels, plafond de
   remise par rôle, ouverture de la remise au Gestionnaire (ADR-0054).
2. Rôles des rapports (ADR-0055 §6).
3. R11 : demandes de support et suivi d'usage détaillé — l'usage par entreprise relève des
   données métier, que la console ne lit jamais (ADR-0031) : définir les indicateurs
   acceptables (agrégats techniques) avant toute implémentation.
4. Domaine, TLS, hébergeur, gestionnaire de secrets, copie hors site, supervision et alertes,
   MFA, paramètres commerciaux (tarifs) : aucune valeur inventée.
5. Opérateurs de paiement du Burkina Faso (point B, en attente de données vérifiées).
