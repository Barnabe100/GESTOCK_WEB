import { useQuery } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { SubscriptionStatus } from '@/core/api/types';

export interface SubscriptionDetails {
  plan_code: string;
  plan_name: string;
  billing_period: 'monthly' | 'annual';
  status: SubscriptionStatus;
  effective_status: SubscriptionStatus;
  started_at: string;
  current_period_start: string;
  current_period_end: string;
  grace_days: number;
  limits: Record<string, number>;
  allowed_access: string[];
  usage: { sites: number; users: number };
}

export function useSubscription() {
  return useQuery({
    queryKey: ['subscription'],
    queryFn: ({ signal }) => api.get<SubscriptionDetails>('/subscription', signal),
  });
}
