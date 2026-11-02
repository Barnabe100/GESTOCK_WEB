import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

/** Palier R10 (ADR-0055) : rapports calculés par le serveur (qui fait foi). */
export const REPORT_VIEW = 'reports.report.view';
export const REPORT_EXPORT = 'reports.report.export';
export const REPORT_EXPORT_FORMATS = ['xlsx', 'csv', 'pdf'] as const;

export type Granularity = 'day' | 'week' | 'month';
export const GRANULARITIES: readonly Granularity[] = ['day', 'week', 'month'];
export type Dimension = 'article' | 'category' | 'site' | 'seller' | 'channel' | 'customer';

/**
 * Coûts et marges : ABSENTS de la réponse sans `catalog.article.cost_view` (jamais remplacés par
 * une valeur) — d'où les champs facultatifs.
 */
export interface SalesFigures {
  sales_count: number;
  revenue: string;
  subtotal: string;
  discounts: string;
  average_basket: string;
  paid: string;
  remaining: string;
  cost?: string;
  margin?: string;
  margin_rate?: string | null;
}

export interface SeriesPoint {
  period: string;
  sales_count: number;
  revenue: string;
  discounts: string;
  cost?: string;
  margin?: string;
}

export interface SalesSummary {
  date_from: string;
  date_to: string;
  granularity: Granularity;
  site_ids: string[];
  totals: SalesFigures;
  series: SeriesPoint[];
}

export interface BreakdownRow {
  key: string | null;
  label: string | null;
  reference: string | null;
  unit: string | null;
  sales_count: number;
  quantity: string | null;
  revenue: string;
  discounts: string | null;
  cost?: string;
  margin?: string;
  margin_rate?: string | null;
  share: string | null;
}

export interface BreakdownPage extends Page<BreakdownRow> {
  revenue_total: string;
}

export interface PaymentMethodRow {
  method: string;
  method_label: string;
  payment_count: number;
  amount: string;
  share: string | null;
}

export interface ProfitabilityRow {
  article_id: string;
  reference: string;
  designation: string;
  unit: string;
  sales_count: number;
  portions: string;
  revenue: string;
  ingredient_cost?: string;
  portion_cost?: string | null;
  margin?: string;
  margin_rate?: string | null;
}

export interface ProfitabilityPage extends Page<ProfitabilityRow> {
  date_from: string;
  date_to: string;
}

export interface ReportFilters {
  date_from: string;
  date_to: string;
  site_id: string | null;
  channel: string | null;
}

/** Paramètres renseignés seulement (le serveur applique ses valeurs par défaut). */
export function filterParams(filters: ReportFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  return params;
}

export const reportKeys = { all: ['reports'] as const };

export function useSalesSummary(filters: ReportFilters, granularity: Granularity) {
  const params = filterParams(filters);
  params.set('granularity', granularity);
  return useQuery({
    queryKey: [...reportKeys.all, 'summary', params.toString()],
    queryFn: ({ signal }) => api.get<SalesSummary>(`/reports/sales/summary?${params}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useBreakdown(dimension: Dimension, query: string) {
  return useQuery({
    queryKey: [...reportKeys.all, 'breakdown', dimension, query],
    queryFn: ({ signal }) =>
      api.get<BreakdownPage>(`/reports/sales/breakdown?dimension=${dimension}&${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function usePaymentMethods(filters: ReportFilters) {
  const params = filterParams(filters);
  return useQuery({
    queryKey: [...reportKeys.all, 'payments', params.toString()],
    queryFn: ({ signal }) => api.get<PaymentMethodRow[]>(`/reports/payments?${params}`, signal),
  });
}

export function useRecipeProfitability(query: string, enabled: boolean) {
  return useQuery({
    queryKey: [...reportKeys.all, 'recipes', query],
    queryFn: ({ signal }) =>
      api.get<ProfitabilityPage>(`/restaurant/recipes/profitability?${query}`, signal),
    enabled,
    placeholderData: keepPreviousData,
  });
}
