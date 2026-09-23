import { useQuery } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { LimitUsage, SubscriptionStatus } from '@/core/api/types';

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
  limits: Record<string, LimitUsage>;
  features: string[];
  allowed_access: string[];
}

export function useSubscription() {
  return useQuery({
    queryKey: ['subscription'],
    queryFn: ({ signal }) => api.get<SubscriptionDetails>('/subscription', signal),
  });
}
