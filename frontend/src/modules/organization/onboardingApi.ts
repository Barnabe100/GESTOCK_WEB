import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { SubscriptionStatus } from '@/core/api/types';

/** Statuts V1 : ils ne font qu'avancer ; `COMPLETED` est définitif. */
export type OnboardingStatus = 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED';

export interface OnboardingAction {
  route: string;
  /** Clé i18n du libellé. */
  label: string;
  permission: string;
  available: boolean;
  blocked_reason: 'subscription_restricted' | 'permission_denied' | null;
}

export interface OnboardingStep {
  code: string;
  order: number;
  required: boolean;
  /** Clés i18n. */
  title: string;
  description: string;
  status: OnboardingStatus;
  completed_at: string | null;
  action: OnboardingAction | null;
}

/** Progression calculée par le serveur (le client ne recalcule rien). */
export interface Onboarding {
  status: OnboardingStatus;
  completed: boolean;
  progress: {
    completed: number;
    total: number;
    percentage: number;
    required_completed: number;
    required_total: number;
  };
  current_step: string | null;
  next_action: OnboardingAction | null;
  subscription_status: SubscriptionStatus;
  steps: OnboardingStep[];
}

const onboardingKey = ['organization', 'onboarding'] as const;

export function useOnboarding(enabled = true) {
  return useQuery({
    queryKey: onboardingKey,
    queryFn: ({ signal }) => api.get<Onboarding>('/onboarding', signal),
    enabled,
    // Toujours réévalué à l'affichage : le serveur constate les progrès réels.
    staleTime: 0,
  });
}

/** Seule transition manuelle : démarrer une étape (jamais la déclarer terminée). */
export function useStartOnboardingStep() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (code: string) =>
      api.patch<Onboarding>(`/onboarding/steps/${code}`, { status: 'IN_PROGRESS' }),
    onSuccess: (data) => queryClient.setQueryData(onboardingKey, data),
  });
}
