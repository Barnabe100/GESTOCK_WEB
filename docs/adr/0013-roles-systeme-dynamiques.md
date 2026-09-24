# ADR-0013 — Rôles système dynamiques

- **Statut** : Proposée (Phase 2.1, à valider)
- **Date** : 2026-09-24

## Contexte

Les rôles système (Administrateur, Consultation…) étaient instanciés à la création d'un
tenant avec une copie figée de leurs permissions. Chaque nouveau module (ex. catalogue)
aurait exigé une migration de données dans **tous** les tenants pour que l'Administrateur
obtienne les nouvelles permissions — une opération transverse impossible proprement sans
privilège de contournement de la RLS (refusé, ADR-0006).

## Décision

- Un rôle système porte seulement son `template_code` ; ses permissions sont **résolues à
  l'exécution** en appliquant les motifs de son modèle (`role_templates.toml` : `*`,
  `*.view`, `catalog.*`…) aux permissions déclarées par le registre.
- Les rôles personnalisés gardent leurs permissions enregistrées (`role_permissions`).
- Modèles livrés : **Administrateur** (`*`), **Consultation** (`*.view`),
  **Gestionnaire de stock** (catalogue, fournisseurs, entrées/sorties sans annulation,
  inventaires, alertes — matrice du Desktop).
- Un modèle absent d'un tenant existant peut être ajouté par le tenant lui-même
  (`POST /roles/from-template`), sans opération plateforme.
- L'anti-escalade et la résolution des capacités utilisent la même fonction
  (`effective_role_permissions`).

## Conséquences

- Ajouter un module enrichit automatiquement les rôles système concernés, dans tous les
  tenants, sans migration.
- Modifier un modèle de rôle modifie les droits de tous ses titulaires : c'est une décision
  produit TechNova, versionnée dans le dépôt.
- Les rôles système restent non modifiables par les tenants ; un besoin spécifique passe par
  un rôle personnalisé.
