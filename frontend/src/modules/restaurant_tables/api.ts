import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

/**
 * Zones et tables des sites de restauration (palier R5, ADR-0049) : configuration seulement.
 * Le serveur fait foi : unicités par site (insensibles à la casse), zone du même site et active,
 * désactivations protégées (zone avec tables actives, table avec commandes en cours).
 */

export const TABLE_VIEW = 'restaurant.tables.table.view';
export const TABLE_MANAGE = 'restaurant.tables.table.manage';

/** Bornes du serveur (`restaurant_tables/schemas.py`) : ordre d'affichage et places. */
export const SORT_ORDER_MAX = 100_000;
export const SEATS_MAX = 999;

export interface Zone {
  id: string;
  site_id: string;
  site_name: string;
  name: string;
  sort_order: number;
  is_active: boolean;
  /** Tables de la zone (toutes) et tables actives. */
  table_count: number;
  active_table_count: number;
  created_at: string;
  updated_at: string;
}

export interface Table {
  id: string;
  site_id: string;
  site_name: string;
  zone_id: string | null;
  zone_name: string | null;
  zone_active: boolean | null;
  label: string;
  seats: number | null;
  sort_order: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ZoneCreateInput {
  site_id: string | null;
  name: string;
  sort_order: number;
}

export interface ZoneUpdateInput {
  name: string;
  sort_order: number;
}

export interface TableCreateInput {
  site_id: string | null;
  zone_id: string | null;
  label: string;
  seats: number | null;
  sort_order: number;
}

/** Remplacement COMPLET des champs modifiables (`PUT`) : le site d'une table ne change jamais. */
export interface TableUpdateInput {
  zone_id: string | null;
  label: string;
  seats: number | null;
  sort_order: number;
}

export const tableKeys = {
  all: ['restaurant.tables'] as const,
  zones: ['restaurant.tables', 'zones'] as const,
  tables: ['restaurant.tables', 'tables'] as const,
};

export function useZones(query: string, enabled = true) {
  return useQuery({
    queryKey: [...tableKeys.zones, query],
    queryFn: ({ signal }) => api.get<Page<Zone>>(`/restaurant/tables/zones?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useTables(query: string, enabled = true) {
  return useQuery({
    queryKey: [...tableKeys.tables, query],
    queryFn: ({ signal }) => api.get<Page<Table>>(`/restaurant/tables?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** Écritures : le serveur contrôle tout ; listes des zones et des tables rafraîchies. */
export function useTableMutations() {
  const qc = useQueryClient();
  const onSuccess = () => void qc.invalidateQueries({ queryKey: tableKeys.all });
  return {
    createZone: useMutation({
      mutationFn: (input: ZoneCreateInput) => api.post<Zone>('/restaurant/tables/zones', input),
      onSuccess,
    }),
    updateZone: useMutation({
      mutationFn: ({ id, input }: { id: string; input: ZoneUpdateInput }) =>
        api.put<Zone>(`/restaurant/tables/zones/${id}`, input),
      onSuccess,
    }),
    setZoneActive: useMutation({
      mutationFn: ({ id, active }: { id: string; active: boolean }) =>
        api.post<Zone>(`/restaurant/tables/zones/${id}/${active ? 'activate' : 'deactivate'}`),
      onSuccess,
    }),
    createTable: useMutation({
      mutationFn: (input: TableCreateInput) => api.post<Table>('/restaurant/tables', input),
      onSuccess,
    }),
    updateTable: useMutation({
      mutationFn: ({ id, input }: { id: string; input: TableUpdateInput }) =>
        api.put<Table>(`/restaurant/tables/${id}`, input),
      onSuccess,
    }),
    setTableActive: useMutation({
      mutationFn: ({ id, active }: { id: string; active: boolean }) =>
        api.post<Table>(`/restaurant/tables/${id}/${active ? 'activate' : 'deactivate'}`),
      onSuccess,
    }),
  };
}
