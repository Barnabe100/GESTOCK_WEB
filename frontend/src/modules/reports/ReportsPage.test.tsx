// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, renderWithCapabilities } from '@/shared/testing';

import type { SalesSummary } from './api';
import ReportsPage from './ReportsPage';

const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s/g, ' ');
const text = (el: Element) => (el.textContent ?? '').replace(/\s/g, ' ');

const SUMMARY: SalesSummary = {
  date_from: '2026-11-01',
  date_to: '2026-11-02',
  granularity: 'day',
  site_ids: ['s1'],
  totals: {
    sales_count: 3,
    revenue: '1000.00',
    subtotal: '1050.00',
    discounts: '50.00',
    average_basket: '333.33',
    paid: '550.00',
    remaining: '450.00',
    cost: '660.00',
    margin: '340.00',
    margin_rate: '34.00',
  },
  series: [
    {
      period: '2026-11-01',
      sales_count: 0,
      revenue: '0.00',
      discounts: '0.00',
      cost: '0.00',
      margin: '0.00',
    },
    {
      period: '2026-11-02',
      sales_count: 3,
      revenue: '1000.00',
      discounts: '50.00',
      cost: '660.00',
      margin: '340.00',
    },
  ],
};

/** Réponse sans `cost_view` : champs de coût ABSENTS (retirés par le serveur). */
function withoutCosts(summary: SalesSummary): SalesSummary {
  const strip = <T extends object>(value: T): T =>
    Object.fromEntries(
      Object.entries(value).filter(([key]) => !['cost', 'margin', 'margin_rate'].includes(key)),
    ) as T;
  return { ...summary, totals: strip(summary.totals), series: summary.series.map(strip) };
}

const BREAKDOWN = {
  items: [
    {
      key: 'a0',
      label: 'Article 0',
      reference: 'A-0',
      unit: 'u',
      sales_count: 3,
      quantity: '6.000',
      revenue: '900.00',
      discounts: null,
      cost: '600.00',
      margin: '300.00',
      margin_rate: '33.33',
      share: '85.71',
    },
  ],
  total: 1,
  limit: 25,
  offset: 0,
  revenue_total: '1050.00',
};

const fetchMock = vi.fn<typeof fetch>();
let summary: SalesSummary = SUMMARY;
const urls = (part: string) =>
  fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => u.includes(part));

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  summary = SUMMARY;
  fetchMock.mockImplementation(async (url) => {
    const u = String(url);
    if (u.includes('/reports/sales/summary')) return jsonResponse(summary);
    if (u.includes('/reports/sales/breakdown')) return jsonResponse(BREAKDOWN);
    if (u.includes('/reports/payments')) return jsonResponse([]);
    return jsonResponse({ items: [], total: 0, limit: 25, offset: 0 });
  });
});
afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const render = (permissions: string[]) =>
  renderWithCapabilities(<ReportsPage />, { permissions, path: '/reports', route: '/reports' });

describe('rapports des ventes', () => {
  it('indicateurs et marge calculés par le serveur ; répartition par article', async () => {
    render(['reports.report.view']);
    await screen.findByLabelText('Indicateurs de la période');
    const metrics = screen.getByLabelText('Indicateurs de la période');
    expect(text(metrics)).toContain(money('1000'));
    expect(text(metrics)).toContain('3 ventes');
    expect(text(metrics)).toContain(money('333.33'));
    expect(text(metrics)).toContain('Taux de marge : 34.00 %');
    // Période par défaut : du 1er du mois à aujourd'hui (fuseau du tenant).
    expect(urls('/reports/sales/summary')[0]).toMatch(/date_from=\d{4}-\d{2}-01/);
    await waitFor(() => expect(screen.getByText('A-0 — Article 0')).toBeTruthy());
    expect(urls('/reports/sales/breakdown')[0]).toContain('dimension=article');
    // Sans la permission d'export : aucune action « Exporter ».
    expect(screen.queryByRole('button', { name: /Exporter/ })).toBeNull();
  });

  it('sans les coûts : ni marge ni coût affichés (jamais de valeur inventée)', async () => {
    summary = withoutCosts(SUMMARY);
    render(['reports.report.view']);
    await screen.findByLabelText('Indicateurs de la période');
    expect(screen.queryByText('Marge')).toBeNull();
    expect(screen.queryByText(/Taux de marge/)).toBeNull();
  });

  it('regroupement et export du périmètre affiché', async () => {
    render(['reports.report.view', 'reports.report.export']);
    await screen.findByLabelText('Indicateurs de la période');
    fireEvent.click(screen.getByText('Mois'));
    await waitFor(() =>
      expect(urls('/reports/sales/summary').at(-1)).toContain('granularity=month'),
    );
    expect(screen.getAllByRole('button', { name: /Exporter/ }).length).toBeGreaterThan(0);
  });
});
