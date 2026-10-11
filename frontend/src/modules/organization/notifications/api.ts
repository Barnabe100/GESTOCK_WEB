import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

/**
 * Notification adressée au membre connecté (ADR-0049 D8) : lisible par lui seul, lu / non lu
 * individuel ; « traitée » (`resolved`) calculée par le serveur à la lecture.
 */
export interface MemberNotification {
  id: string;
  kind: string;
  site: { id: string; name: string; code: string };
  entity_type: string;
  entity_id: string;
  payload: { daily_number?: number; line_count?: number; business_date?: string };
  created_at: string;
  read_at: string | null;
  resolved: boolean | null;
}

export const MEMBER_NOTIFICATIONS_KEY = ['member-notifications'] as const;
/** Interrogation périodique (D8 : 10 à 15 s ; aucun WebSocket en V1). */
export const MEMBER_NOTIFICATIONS_POLL_MS = 15_000;

export function useMemberNotifications(query: string) {
  return useQuery({
    queryKey: [...MEMBER_NOTIFICATIONS_KEY, 'list', query],
    queryFn: ({ signal }) =>
      api.get<Page<MemberNotification>>(`/member-notifications?${query}`, signal),
    placeholderData: (previous) => previous,
    refetchInterval: MEMBER_NOTIFICATIONS_POLL_MS,
  });
}

/** Marque une notification (ou toutes, ``id`` nul) comme lue par le membre. */
export function useMarkMemberNotificationsRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string | null) =>
      api.post<void>(
        id
          ? `/member-notifications/${encodeURIComponent(id)}/read`
          : '/member-notifications/read-all',
        {},
      ),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: MEMBER_NOTIFICATIONS_KEY }),
  });
}
