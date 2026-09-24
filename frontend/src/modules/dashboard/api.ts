import { useQuery } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { AlertSummary } from '@/modules/alerts/api';
import type { Sale } from '@/modules/sales/api';
import type { Page } from '@/shared/lib/serverTable';

/**
 * Lectures du tableau de bord. Chaque requête n'est lancée que si l'utilisateur détient la
 * permission de consultation correspondante (le backend la contrôle de toute façon).
 */
export function useAlertSummary(enabled: boolean) {
  return useQuery({
    queryKey: ['alerts', 'stock', 'summary'],
    queryFn: ({ signal }) => api.get<AlertSummary>('/alerts/stock/summary', signal),
    enabled,
  });
}

/** Nombre de documents correspondant au filtre (seul le total de la page est utilisé). */
export function useDocumentCount(path: string, query: string, enabled: boolean) {
  return useQuery({
    queryKey: ['dashboard', 'count', path, query],
    queryFn: ({ signal }) => api.get<Page<unknown>>(`${path}?limit=1&${query}`, signal),
    select: (page) => page.total,
    enabled,
  });
}

export function useRecentSales(enabled: boolean) {
  return useQuery({
    queryKey: ['sales', 'list', 'dashboard-recent'],
    queryFn: ({ signal }) => api.get<Page<Sale>>('/sales?limit=5&sort=-number', signal),
    enabled,
  });
}
