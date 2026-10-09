import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

/** Menu des sites de restauration (palier R1, ADR-0049) : montants en chaînes décimales. */

export const MENU_VIEW = 'restaurant.menu.view';
export const MENU_MANAGE = 'restaurant.menu.manage';
export const MENU_AVAILABILITY = 'restaurant.menu.availability';

/** « Épuisé » visible en 15 s au plus (critère R1) : actualisation périodique des listes. */
export const MENU_REFRESH_MS = 15_000;

export interface MenuSection {
  id: string;
  site_id: string;
  site_name: string;
  name: string;
  sort_order: number;
  is_active: boolean;
  item_count: number;
  created_at: string;
  updated_at: string;
}

/** Motif pour lequel un élément n'est pas commandable (code stable, calculé par le serveur). */
export type MenuBlocker =
  | 'item_inactive'
  | 'section_inactive'
  | 'unavailable'
  | 'article_inactive'
  | 'article_not_in_site_assortment'
  | 'packaging_inactive'
  | 'packaging_price_not_set';

export interface MenuItem {
  id: string;
  site_id: string;
  site_name: string;
  section_id: string;
  section_name: string;
  section_active: boolean;
  article_id: string;
  reference: string;
  designation: string;
  unit: string;
  packaging_id: string | null;
  packaging_name: string | null;
  conversion: string | null;
  display_name: string | null;
  description: string | null;
  sort_order: number;
  is_active: boolean;
  available: boolean;
  unavailable_reason: string | null;
  /** Prix du catalogue (aucun prix par site) ; `null` : conditionnement sans prix ou inactif. */
  price: string | null;
  orderable: boolean;
  blockers: MenuBlocker[];
  created_at: string;
  updated_at: string;
}

export interface SectionInput {
  site_id?: string | null;
  name: string;
  sort_order: number;
}

export interface ItemCreateInput {
  site_id: string | null;
  section_id: string;
  article_id: string;
  packaging_id: string | null;
  display_name: string | null;
  description: string | null;
  sort_order: number;
}

export interface ItemUpdateInput {
  section_id: string;
  display_name: string | null;
  description: string | null;
  sort_order: number;
}

export const menuKeys = {
  all: ['restaurant.menu'] as const,
  sections: ['restaurant.menu', 'sections'] as const,
  items: ['restaurant.menu', 'items'] as const,
};

export function useMenuSections(query: string, enabled = true) {
  return useQuery({
    queryKey: [...menuKeys.sections, query],
    queryFn: ({ signal }) =>
      api.get<Page<MenuSection>>(`/restaurant/menu/sections?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useMenuItems(query: string, enabled = true) {
  return useQuery({
    queryKey: [...menuKeys.items, query],
    queryFn: ({ signal }) => api.get<Page<MenuItem>>(`/restaurant/menu/items?${query}`, signal),
    placeholderData: keepPreviousData,
    refetchInterval: MENU_REFRESH_MS,
    enabled,
  });
}

/** Écritures du menu : le serveur fait foi (contrôles, unicités) ; listes rafraîchies. */
export function useMenuMutations() {
  const qc = useQueryClient();
  const onSuccess = () => void qc.invalidateQueries({ queryKey: menuKeys.all });
  return {
    createSection: useMutation({
      mutationFn: (input: SectionInput) =>
        api.post<MenuSection>('/restaurant/menu/sections', input),
      onSuccess,
    }),
    updateSection: useMutation({
      mutationFn: ({ id, input }: { id: string; input: SectionInput }) =>
        api.put<MenuSection>(`/restaurant/menu/sections/${id}`, {
          name: input.name,
          sort_order: input.sort_order,
        }),
      onSuccess,
    }),
    setSectionActive: useMutation({
      mutationFn: ({ id, active }: { id: string; active: boolean }) =>
        api.post<MenuSection>(
          `/restaurant/menu/sections/${id}/${active ? 'activate' : 'deactivate'}`,
        ),
      onSuccess,
    }),
    createItem: useMutation({
      mutationFn: (input: ItemCreateInput) => api.post<MenuItem>('/restaurant/menu/items', input),
      onSuccess,
    }),
    updateItem: useMutation({
      mutationFn: ({ id, input }: { id: string; input: ItemUpdateInput }) =>
        api.put<MenuItem>(`/restaurant/menu/items/${id}`, input),
      onSuccess,
    }),
    setItemActive: useMutation({
      mutationFn: ({ id, active }: { id: string; active: boolean }) =>
        api.post<MenuItem>(`/restaurant/menu/items/${id}/${active ? 'activate' : 'deactivate'}`),
      onSuccess,
    }),
    setAvailability: useMutation({
      mutationFn: ({
        id,
        available,
        reason,
      }: {
        id: string;
        available: boolean;
        reason: string | null;
      }) => api.put<MenuItem>(`/restaurant/menu/items/${id}/availability`, { available, reason }),
      onSuccess,
    }),
  };
}

/** Libellé affiché d'un élément : nom propre au menu, sinon désignation (et présentation). */
export function itemLabel(item: MenuItem): string {
  const base = item.display_name ?? item.designation;
  return item.packaging_name && !item.display_name ? `${base} — ${item.packaging_name}` : base;
}
