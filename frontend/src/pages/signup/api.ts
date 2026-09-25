import { useQuery } from '@tanstack/react-query';

import { api } from '@/core/api/client';

/** Données publiques de l'inscription : servies par le backend, jamais embarquées (pays :
 * `@/core/api/geo`). */

export interface PublicSector {
  code: string;
  name: string;
  icon: string | null;
}

export interface PublicProfile {
  code: string;
  name: string;
  description: string | null;
  sector: string | null;
}

export interface PublicPlanPeriod {
  billing_period: 'monthly' | 'annual';
  /** Nul : prix non affiché par TechNova. */
  price: string | null;
}

export interface PublicPlan {
  code: string;
  name: string;
  description: string | null;
  contact_required: boolean;
  self_service: boolean;
  trial_days: number;
  price_displayed: boolean;
  currency: string | null;
  periods: PublicPlanPeriod[];
  limits: Record<string, number | null>;
  modules: string[];
}

const STABLE = { staleTime: Infinity, retry: 1 } as const;

export function usePublicProfiles() {
  return useQuery({
    queryKey: ['public', 'business-profiles'],
    queryFn: ({ signal }) =>
      api.get<{ sectors: PublicSector[]; profiles: PublicProfile[] }>(
        '/public/business-profiles',
        signal,
      ),
    ...STABLE,
  });
}

export function usePublicPlans() {
  return useQuery({
    queryKey: ['public', 'plans'],
    queryFn: ({ signal }) =>
      api.get<{ contact_email: string | null; plans: PublicPlan[] }>('/public/plans', signal),
    ...STABLE,
  });
}
