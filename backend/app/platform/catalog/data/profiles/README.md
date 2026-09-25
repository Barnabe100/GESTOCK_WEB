# Profils d'activité (Business Profiles)

Un fichier par profil : `profiles/<secteur>/<activité>.toml`, code `"<secteur>.<activité>"`.
Format et règles : [`docs/architecture/BUSINESS_PROFILES.md`](../../../../../../docs/architecture/BUSINESS_PROFILES.md).

Champs : `code`, `sector`, `ux_profile`, `name`, `description`, `sort_order`, `is_active`
(défaut `true`) ; surcharges facultatives du profil UX : `modules`, `optional_modules`,
`[[navigation]]` (remplace), `[dashboard]` (remplace), `[terminology.<langue>]` (fusion),
`[theme]` (fusion).
