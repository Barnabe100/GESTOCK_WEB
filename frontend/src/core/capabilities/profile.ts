import type { TFunction } from 'i18next';

/**
 * Libellés du profil d'activité et du secteur : traduits par leur code technique
 * (`businessProfiles.<secteur>.<activité>`, `sectors.<code>`), avec repli sur le nom du
 * catalogue. Aucun composant ne compare un code de profil ou de secteur.
 */
export function profileLabel(t: TFunction, profile: { code: string; name: string }): string {
  return t(`businessProfiles.${profile.code}`, { defaultValue: profile.name });
}

export function sectorLabel(t: TFunction, sector: { code: string; name: string }): string {
  return t(`sectors.${sector.code}`, { defaultValue: sector.name });
}
