import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { StockLevel } from '@/modules/stock/api';
import type { Page } from '@/shared/lib/serverTable';

export type AlertStateFilter = 'alerts' | 'out' | 'low';

export interface AlertSummary {
  out: number;
  low: number;
}

export function useStockAlerts(query: string) {
  return useQuery({
    queryKey: ['alerts', 'stock', query],
    queryFn: ({ signal }) => api.get<Page<StockLevel>>(`/alerts/stock?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useStockAlertSummary() {
  return useQuery({
    queryKey: ['alerts', 'stock', 'summary'],
    queryFn: ({ signal }) => api.get<AlertSummary>('/alerts/stock/summary', signal),
  });
}
