# ADR-0013 — Rôles système dynamiques

- **Statut** : Acceptée (validée le 2026-09-24 ; complétée par l'[ADR-0015](0015-rbac-roles-de-base-et-personnalises.md))
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
- Modèles livrés : voir l'ADR-0015 (Administrateur, Gestionnaire, Vendeur, Consultant). Les
  motifs peuvent exclure des permissions (`exclude`) ; un modèle peut être `protected`.
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

## Avenant (2026-09-24, ADR-0015)

- Nom et description d'un rôle de base sont eux aussi lus depuis son modèle à l'exécution.
- Les modèles ne déclarent **aucune** permission de module non développé : ils sont complétés
  quand le module existe (modification du fichier, sans migration).
- Rôles de base désactivables par le tenant, sauf un rôle `protected` (Administrateur).
