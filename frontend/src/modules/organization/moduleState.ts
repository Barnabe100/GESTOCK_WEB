import type { Tone } from '@/shared/ui/StatusBadge';

import type { SiteModule } from './api';

/**
 * État d'un module sur UN site (palier E), déduit uniquement des champs du serveur
 * (`status`, `in_profile`, `in_plan`, `activated_for_site`, `effective`) — aucun état inventé.
 */
export type ModuleState =
  | 'active' // disponible et activé (effectif)
  | 'blocked' // activé mais non effectif : un module requis est inactif sur ce site
  | 'inactive' // disponible mais désactivé
  | 'notInPlan' // proposé par le profil, hors de l'abonnement du site
  | 'planned' // fonctionnalité future, non implémentée
  | 'notInProfile'; // non proposé par le profil du site (proposé sur un autre site)

export const MODULE_STATE_ORDER: readonly ModuleState[] = [
  'active',
  'blocked',
  'inactive',
  'notInPlan',
  'planned',
  'notInProfile',
];

export const MODULE_STATE_TONES: Record<ModuleState, Tone> = {
  active: 'success',
  blocked: 'warning',
  inactive: 'neutral',
  notInPlan: 'warning',
  planned: 'info',
  notInProfile: 'neutral',
};

export function moduleState(module: Pick<SiteModule, keyof SiteModule>): ModuleState {
  if (!module.in_profile) return 'notInProfile';
  if (module.status === 'planned') return 'planned';
  if (!module.in_plan) return 'notInPlan';
  if (module.effective) return 'active';
  if (module.activated_for_site) return 'blocked';
  return 'inactive';
}

/** Un interrupteur n'est proposé que si l'activation est réellement possible sur ce site. */
export function canToggle(state: ModuleState): boolean {
  return state === 'active' || state === 'blocked' || state === 'inactive';
}
