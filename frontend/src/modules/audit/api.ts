import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { api } from '@/core/api/client';

export interface AuditLog {
  id: string;
  occurred_at: string;
  action: string;
  user_id: string | null;
  user_email: string | null;
  user_name: string | null;
  site_id: string | null;
  entity_type: string | null;
  entity_id: string | null;
  data: Record<string, unknown>;
  ip_address: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export function useAuditLogs(limit: number, offset: number) {
  return useQuery({
    queryKey: ['audit', 'logs', limit, offset],
    queryFn: ({ signal }) =>
      api.get<Page<AuditLog>>(`/audit-logs?limit=${limit}&offset=${offset}`, signal),
    placeholderData: keepPreviousData,
  });
}
