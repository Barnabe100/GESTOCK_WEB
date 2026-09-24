import { useEffect, useState } from 'react';

export type StatusFilterValue = 'all' | 'active' | 'inactive';

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** État d'un tableau paginé et trié côté serveur. */
export interface TableState {
  first: number;
  rows: number;
  sortField?: string;
  sortOrder?: 1 | -1 | 0 | null;
}

export const INITIAL_TABLE: TableState = { first: 0, rows: 25 };

/** Paramètres de requête `limit/offset/sort` (+ filtres non vides). */
export function toQueryString(
  state: TableState,
  filters: Record<string, string | null | undefined> = {},
): string {
  const params = new URLSearchParams({ limit: String(state.rows), offset: String(state.first) });
  if (state.sortField && state.sortOrder) {
    params.set('sort', `${state.sortOrder === -1 ? '-' : ''}${state.sortField}`);
  }
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  return params.toString();
}

/** Valeur stabilisée après une pause de saisie (recherche). */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
