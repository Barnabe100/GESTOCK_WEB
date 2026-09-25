// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';

import DashboardPage from './DashboardPage';

const SALE = {
  id: 'v1',
  number: 'VTE-000001',
  site_id: 's1',
  site_name: 'Boutique',
  customer_id: null,
  customer_code: null,
  customer_name: null,
  status: 'VALIDATED',
  sale_date: '2026-09-24',
  total: '15000.00',
};

const called = (fetchMock: ReturnType<typeof vi.fn<typeof fetch>>, fragment: string) =>
  fetchMock.mock.calls.some(([url]) => String(url).includes(fragment));

describe('tableau de bord', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/alerts/stock/summary')) return jsonResponse({ out: 2, low: 5 });
      if (u.includes('/sales?limit=1&status=DRAFT'))
        return jsonResponse({ items: [], total: 3, limit: 1, offset: 0 });
      if (u.includes('/sales?limit=1&status=VALIDATED'))
        return jsonResponse({ items: [], total: 7, limit: 1, offset: 0 });
      if (u.includes('/cash/sessions?limit=1'))
        return jsonResponse({ items: [], total: 1, limit: 1, offset: 0 });
      if (u.includes('/receivables/summary'))
        return jsonResponse({
          total_receivables: '45000.00',
          receivables_count: 2,
          debtor_customers_count: 2,
        });
      if (u.includes('/stock/transfers?limit=1'))
        return jsonResponse({ items: [], total: 1, limit: 1, offset: 0 });
      return pageOf([SALE]);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('affiche indicateurs, dernières ventes et actions rapides selon les permissions', async () => {
    renderWithCapabilities(<DashboardPage />, {
      permissions: [
        'alerts.stock.view',
        'sales.sale.view',
        'sales.sale.create',
        'stock.transfer.view',
        'stock.transfer.create',
      ],
      features: ['stock.transfers'],
    });
    const indicators = screen.getByRole('region', { name: 'Indicateurs' });
    expect(await within(indicators).findByText('2')).toBeTruthy();
    expect(within(indicators).getByText('Articles en rupture')).toBeTruthy();
    expect(await within(indicators).findByText('5')).toBeTruthy();
    expect(await within(indicators).findByText('3')).toBeTruthy();
    expect(await within(indicators).findByText('7')).toBeTruthy();
    expect(within(indicators).getByText("Ventes validées aujourd'hui")).toBeTruthy();
    expect(within(indicators).getByText('Transferts en brouillon')).toBeTruthy();
    expect(await screen.findByText('VTE-000001')).toBeTruthy();
    const shortcuts = screen.getByRole('navigation', { name: 'Actions rapides' });
    expect(within(shortcuts).getByRole('button', { name: 'Nouvelle vente' })).toBeTruthy();
    expect(within(shortcuts).getByRole('button', { name: 'Nouveau transfert' })).toBeTruthy();
  });

  it('sans permission ni fonctionnalité : ni indicateur ni requête ni raccourci correspondant', async () => {
    renderWithCapabilities(<DashboardPage />, {
      // Consultation de l'historique des transferts, mais fonctionnalité absente du plan.
      permissions: ['stock.transfer.view', 'stock.transfer.create'],
      features: [],
    });
    expect(await screen.findByText('Transferts en brouillon')).toBeTruthy();
    expect(screen.queryByText('Articles en rupture')).toBeNull();
    expect(screen.queryByText('Dernières ventes')).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Actions rapides' })).toBeNull();
    expect(called(fetchMock, '/alerts/')).toBe(false);
    expect(called(fetchMock, '/sales')).toBe(false);
    // L'abonnement reste affiché (données des capacités).
    expect(screen.getByText(/Standard/)).toBeTruthy();
  });
});

const UX = (widgets: string[], shortcuts: string[], upcoming: string[] = []) => ({
  code: 'restaurant.default',
  navigation: [],
  dashboard: { widgets, shortcuts },
  theme: { accent: 'orange' as const, density: 'comfortable' as const, icon: null },
  upcoming,
});

describe('tableau de bord selon le profil UX', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/cash/sessions?limit=1'))
        return jsonResponse({ items: [], total: 2, limit: 1, offset: 0 });
      if (u.includes('/receivables/summary'))
        return jsonResponse({
          total_receivables: '45000.00',
          receivables_count: 2,
          debtor_customers_count: 2,
        });
      if (u.includes('/sales?limit=1'))
        return jsonResponse({ items: [], total: 4, limit: 1, offset: 0 });
      return pageOf([SALE]);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('ordre du profil ; widgets de modules futurs ignorés ; raccourcis déclarés', async () => {
    renderWithCapabilities(<DashboardPage />, {
      permissions: [
        'sales.sale.view',
        'sales.sale.create',
        'cash_register.session.view',
        'receivables.receivable.view',
        'pos.terminal.use',
        'stock.entry.create',
      ],
      ux: UX(
        [
          'sales:today',
          'cash_register:open_sessions',
          'restaurant.tables:occupied',
          'receivables:outstanding',
        ],
        ['pos:open', 'sales:new'],
        ['restaurant.tables', 'restaurant.kitchen'],
      ),
    });
    const indicators = screen.getByRole('region', { name: 'Indicateurs' });
    const labels = within(indicators)
      .getAllByRole('link')
      .map((link) => link.querySelector('.sm-metric-label')?.textContent);
    expect(labels).toEqual([
      "Ventes validées aujourd'hui",
      'Caisses ouvertes',
      'Créances ouvertes',
    ]);
    expect(await within(indicators).findByText(/45\s000/)).toBeTruthy();
    expect(within(indicators).getByText('2 clients débiteurs')).toBeTruthy();
    // Aucun panneau « Dernières ventes » : non déclaré par ce profil.
    expect(screen.queryByText('Dernières ventes')).toBeNull();
    const shortcuts = screen.getByRole('navigation', { name: 'Actions rapides' });
    expect(
      within(shortcuts)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Point de vente', 'Nouvelle vente']);
    // Modules futurs du profil : annoncés « bientôt disponibles », sans lien.
    const upcoming = screen.getByRole('list', { name: 'À venir pour votre activité' });
    expect(within(upcoming).getByText('Tables')).toBeTruthy();
    expect(within(upcoming).getByText('Cuisine')).toBeTruthy();
    expect(within(upcoming).queryAllByRole('link')).toEqual([]);
    // Secteur et profil traduits par leur code (catalogue : « Alimentation »).
    expect(screen.getByText('Commerce de détail')).toBeTruthy();
    expect(screen.getByText('Alimentation / Supérette')).toBeTruthy();
  });

  it('un widget déclaré sans la permission correspondante ne lance aucune requête', () => {
    renderWithCapabilities(<DashboardPage />, {
      permissions: ['sales.sale.view'],
      ux: UX(
        ['cash_register:open_sessions', 'receivables:outstanding', 'sales:drafts'],
        ['pos:open'],
      ),
    });
    expect(screen.getByText('Ventes en brouillon')).toBeTruthy();
    expect(screen.queryByText('Caisses ouvertes')).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Actions rapides' })).toBeNull();
    expect(called(fetchMock, '/cash/')).toBe(false);
    expect(called(fetchMock, '/receivables')).toBe(false);
  });
});

describe('abonnement en attente d’activation', () => {
  afterEach(cleanup);

  it('explique ce qui reste possible, sans message générique', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => pageOf([])),
    );
    renderWithCapabilities(<DashboardPage />, {
      permissions: ['organization.site.manage'],
      subscriptionStatus: 'pending_activation',
    });
    expect(screen.getByTestId('pending-activation').textContent).toContain(
      "en attente d'activation",
    );
    expect(screen.getByText("En attente d'activation")).toBeTruthy();
    vi.unstubAllGlobals();
  });
});
