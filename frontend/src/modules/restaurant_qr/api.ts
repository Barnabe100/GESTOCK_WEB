import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

/**
 * Points QR d'un site et réglages QR (palier R7, ADR-0052). Le jeton d'un point n'est JAMAIS
 * relu : l'adresse et l'image du QR Code n'existent que dans la réponse d'émission (création ou
 * régénération). Le serveur construit l'adresse depuis sa configuration (jamais le navigateur).
 */

export const POINT_VIEW = 'restaurant.qr.point.view';
export const POINT_MANAGE = 'restaurant.qr.point.manage';
export const QR_SETTINGS_MANAGE = 'restaurant.qr.settings.manage';

export type QrPointKind = 'TABLE' | 'COUNTER';
export type ContactFieldMode = 'HIDDEN' | 'OPTIONAL' | 'REQUIRED';
export const CONTACT_MODES: readonly ContactFieldMode[] = ['HIDDEN', 'OPTIONAL', 'REQUIRED'];

export interface QrPoint {
  id: string;
  site_id: string;
  site_name: string;
  kind: QrPointKind;
  table_id: string | null;
  table_label: string | null;
  table_active: boolean | null;
  zone_name: string | null;
  label: string | null;
  display_name: string;
  is_active: boolean;
  token_generated_at: string;
  token_generated_by_name: string | null;
  created_at: string;
  updated_at: string;
}

/** Émission : seule réponse portant l'adresse et le SVG (Segno, côté serveur). */
export interface QrPointIssued {
  point: QrPoint;
  url: string;
  svg: string;
}

export interface QrSettings {
  site_id: string;
  max_lines: number;
  max_quantity_per_line: number;
  max_pending_per_point: number;
  customer_name_mode: ContactFieldMode;
  customer_phone_mode: ContactFieldMode;
  platform_max_lines: number;
  platform_max_quantity_per_line: number;
  platform_max_pending_per_point: number;
  updated_at: string;
}

export type QrSettingsInput = Pick<
  QrSettings,
  | 'max_lines'
  | 'max_quantity_per_line'
  | 'max_pending_per_point'
  | 'customer_name_mode'
  | 'customer_phone_mode'
>;

const keys = {
  points: ['restaurant.qr', 'points'] as const,
  settings: (siteId: string | null) => ['restaurant.qr', 'settings', siteId] as const,
};

export function useQrPoints(query: string) {
  return useQuery({
    queryKey: [...keys.points, query],
    queryFn: ({ signal }) => api.get<Page<QrPoint>>(`/restaurant/qr/points?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useQrSettings(siteId: string | null) {
  return useQuery({
    queryKey: keys.settings(siteId),
    queryFn: ({ signal }) =>
      api.get<QrSettings>(`/restaurant/qr/settings?site_id=${siteId ?? ''}`, signal),
    enabled: siteId !== null,
  });
}

export function useQrMutations() {
  const qc = useQueryClient();
  const onSuccess = () => void qc.invalidateQueries({ queryKey: keys.points });
  return {
    create: useMutation({
      mutationFn: (input: {
        site_id: string;
        kind: QrPointKind;
        table_id?: string | null;
        label?: string | null;
      }) => api.post<QrPointIssued>('/restaurant/qr/points', input),
      onSuccess,
    }),
    rename: useMutation({
      mutationFn: ({ id, label }: { id: string; label: string }) =>
        api.put<QrPoint>(`/restaurant/qr/points/${id}`, { label }),
      onSuccess,
    }),
    setActive: useMutation({
      mutationFn: ({ id, active }: { id: string; active: boolean }) =>
        api.post<QrPoint>(`/restaurant/qr/points/${id}/${active ? 'activate' : 'deactivate'}`),
      onSuccess,
    }),
    regenerate: useMutation({
      mutationFn: (id: string) =>
        api.post<QrPointIssued>(`/restaurant/qr/points/${id}/regenerate`, {
          confirm_invalidation: true,
        }),
      onSuccess,
    }),
    updateSettings: useMutation({
      mutationFn: ({ siteId, input }: { siteId: string; input: QrSettingsInput }) =>
        api.put<QrSettings>('/restaurant/qr/settings', { site_id: siteId, ...input }),
      onSuccess: (data) => qc.setQueryData(keys.settings(data.site_id), data),
    }),
  };
}
