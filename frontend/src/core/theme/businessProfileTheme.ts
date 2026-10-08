import type { TFunction } from 'i18next';

import type { Capabilities, UxAccent } from '@/core/api/types';
import { profileLabel, sectorLabel } from '@/core/capabilities/profile';
import { buildNavigationSections } from '@/core/modules/registry';
import type { FrontendModule } from '@/core/modules/types';

/**
 * Thème métier (palier E) : présentation dérivée du profil d'activité du SITE actif.
 *
 * Profil du site → BusinessProfileTheme → Design System (jetons `--sm-*`) → pages. Tout vient des
 * données du catalogue exposées par les capacités (profil UX : accent, densité, icône ; secteur :
 * icône ; navigation du profil : modules mis en avant) : aucun code ne compare un code de profil
 * ou de secteur, et un profil sans thème retombe sur le thème neutre StockManager. Présentation
 * seulement : les droits restent ceux des capacités.
 */
export interface BusinessProfileTheme {
  /** Thème neutre StockManager (profil absent, inconnu ou sans thème). */
  fallback: boolean;
  colors: {
    /** Accent principal (palette contrôlée `data-accent`) ; l'accent secondaire en dérive. */
    accent: UxAccent;
  };
  density: 'comfortable' | 'compact';
  icons: {
    /** Icône du profil (thème du profil, sinon celle du secteur). */
    profile: string;
    sector: string;
  };
  visuals: {
    /** Illustration du profil : icône principale et motifs (modules mis en avant). */
    illustration: { icon: string; motifs: string[] };
  };
  labels: {
    profile: string | null;
    sector: string | null;
    /** `site` : profil du site sélectionné ; `reference` : vue « Tous les sites ». */
    scope: 'site' | 'reference';
    site: string | null;
  };
  dashboard: { widgets: string[]; shortcuts: string[] };
}

export const NEUTRAL_ICON = 'pi pi-briefcase';
const NEUTRAL_ACCENT: UxAccent = 'blue';
const ACCENTS: readonly UxAccent[] = ['blue', 'green', 'orange', 'teal', 'indigo'];

export function resolveBusinessProfileTheme(
  caps: Capabilities,
  t: TFunction,
  modules: readonly FrontendModule[] = [],
): BusinessProfileTheme {
  const theme = caps.ux?.theme;
  const sector = caps.profile?.sector ?? null;
  const known = Boolean(caps.profile?.code);
  const accent = theme?.accent && ACCENTS.includes(theme.accent) ? theme.accent : null;
  const sectorIcon = sector?.icon ?? NEUTRAL_ICON;
  const profileIcon = theme?.icon ?? sectorIcon;
  // Motifs : icônes des premières entrées du menu du profil (modules effectifs du site) —
  // un entrepôt met le stock en avant, un commerce la vente ; rien de simulé.
  const motifs =
    modules.length && Array.isArray(caps.ux?.navigation) && Array.isArray(caps.navigation)
      ? buildNavigationSections(modules, caps)
          .filter((section) => section.group !== 'home')
          .flatMap((section) => section.items.map((item) => item.icon))
          .filter((icon, index, all) => all.indexOf(icon) === index)
          .slice(0, 3)
      : [];
  return {
    fallback: !known || accent === null,
    colors: { accent: accent ?? NEUTRAL_ACCENT },
    density: theme?.density ?? 'comfortable',
    icons: { profile: known ? profileIcon : NEUTRAL_ICON, sector: sectorIcon },
    visuals: { illustration: { icon: known ? profileIcon : NEUTRAL_ICON, motifs } },
    labels: {
      profile: known ? profileLabel(t, caps.profile) : null,
      sector: sector ? sectorLabel(t, sector) : null,
      scope: caps.profile_scope ?? 'site',
      site: caps.site?.name ?? null,
    },
    dashboard: {
      widgets: caps.ux?.dashboard?.widgets ?? [],
      shortcuts: caps.ux?.dashboard?.shortcuts ?? [],
    },
  };
}
