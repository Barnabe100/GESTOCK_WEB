// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';

import SalesPage from './SalesPage';

const sale = (over: Record<string, unknown>) => ({
  id: 'v1',
  number: 'VENT-BOU-2026-000001',
  site_id: 's1',
  site_name: 'Boutique',
  customer_id: 'c1',
  customer_code: 'CLI-000001',
  customer_name: 'Awa Ouédraogo',
  status: 'VALIDATED',
  channel: 'BACKOFFICE',
  sale_date: '2026-09-24',
  notes: null,
  subtotal: '4500.00',
  total: '4500.00',
  line_count: 1,
  created_at: '2026-09-24T08:00:00Z',
  updated_at: '2026-09-24T08:00:00Z',
  created_by_name: 'Moussa',
  validated_at: null,
  validated_by_name: null,
  cancelled_at: null,
  cancelled_by_name: null,
  cancellation_reason: null,
  lines: [],
  ...over,
});

describe('liste des ventes', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url) => {
      const path = String(url);
      if (path.includes('/sales/sellers')) {
        return jsonResponse([
          { id: 'u-moussa', name: 'Moussa' },
          { id: 'u-awa', name: 'Awa' },
        ]);
      }
      if (path.includes('/sales/export')) {
        return new Response('Numéro;Date', {
          status: 200,
          headers: {
            'Content-Type': 'text/csv; charset=utf-8',
            'Content-Disposition': 'attachment; filename="ventes-20260924-0800.csv"',
          },
        });
      }
      return pageOf([
        sale({
          payment_status: 'PARTIALLY_PAID',
          paid_amount: '1500.00',
          remaining_amount: '3000.00',
        }),
        sale({
          id: 'v2',
          // Brouillon : aucun numéro (attribué à la validation).
          number: null,
          customer_id: null,
          customer_code: null,
          customer_name: null,
          status: 'DRAFT',
          total: '1200.00',
        }),
      ]);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('affiche numéro, client, montant, statut et vendeur', async () => {
    renderWithCapabilities(<SalesPage />, { permissions: ['sales.sale.view'] });
    expect(await screen.findByText('VENT-BOU-2026-000001')).toBeTruthy();
    expect(screen.getByText('Awa Ouédraogo')).toBeTruthy();
    expect(screen.getByText('Ordinaire')).toBeTruthy();
    expect(screen.getByText(formatMoney('4500.00', 'XOF', 'fr').replace(/\s/g, ' '))).toBeTruthy();
    expect(screen.getByText('Validée')).toBeTruthy();
    expect(screen.getByText('Brouillon')).toBeTruthy();
    expect(screen.getAllByText('Moussa')).toHaveLength(2);
    // Plusieurs sites accessibles : colonne et filtre de site.
    expect(screen.getAllByText('Boutique').length).toBeGreaterThan(0);
    // Consultation seule : pas de création.
    expect(screen.queryByRole('button', { name: 'Nouvelle vente' })).toBeNull();
  });

  it('propose la création avec la permission et filtre côté serveur', async () => {
    renderWithCapabilities(<SalesPage />, {
      permissions: ['sales.sale.view', 'sales.sale.create'],
    });
    expect(await screen.findByRole('button', { name: 'Nouvelle vente' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Du'), { target: { value: '2026-09-01' } });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes('date_from=2026-09-01')),
      ).toBe(true),
    );
    fireEvent.change(screen.getByPlaceholderText('Numéro, client (code, nom, téléphone)…'), {
      target: { value: 'awa' },
    });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('search=awa'))).toBe(true),
    );
    const last = String(fetchMock.mock.calls.at(-1)?.[0]);
    expect(last).toContain('/api/v1/sales?');
    // Tri par défaut : la plus récente d'abord (création), jamais l'ordre du numéro.
    expect(last).toContain('sort=-created_at');
  });

  it('état d’encaissement distinct du statut, filtre serveur par état', async () => {
    renderWithCapabilities(<SalesPage />, { permissions: ['sales.sale.view'] });
    const validated = (await screen.findByText('VENT-BOU-2026-000001')).closest(
      'tr',
    ) as HTMLElement;
    expect(within(validated).getByText('Validée')).toBeTruthy();
    expect(within(validated).getByText('Partiellement payée')).toBeTruthy();
    const draft = screen.getByText('non numérotée').closest('tr') as HTMLElement;
    expect(within(draft).getByText('—')).toBeTruthy(); // brouillon : pas d'encaissement
    const filter = screen.getByLabelText('Paiement', { selector: 'input, select, span, div' });
    fireEvent.click(filter.closest('.p-dropdown') ?? filter);
    fireEvent.click(
      screen.getAllByRole('option', { name: 'Non payée', hidden: true }).at(-1) as Element,
    );
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes('payment_status=UNPAID'))).toBe(
        true,
      ),
    );
  });

  const salesCalls = () =>
    fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => u.includes('/api/v1/sales?'));

  it('filtre « Mes ventes », vendeur et filtres avancés côté serveur', async () => {
    renderWithCapabilities(<SalesPage />, { permissions: ['sales.sale.view'] });
    await screen.findByText('VENT-BOU-2026-000001');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Mes ventes' }));
    await waitFor(() => expect(salesCalls().at(-1)).toContain('mine=true'));

    const seller = screen.getByLabelText('Vendeur / opérateur', {
      selector: 'input, select, span, div',
    });
    fireEvent.click(seller.closest('.p-dropdown') ?? seller);
    fireEvent.click(screen.getAllByRole('option', { name: 'Awa', hidden: true }).at(-1) as Element);
    await waitFor(() => expect(salesCalls().at(-1)).toContain('seller_id=u-awa'));

    fireEvent.click(screen.getByRole('button', { name: 'Plus de filtres' }));
    // Références distinctes : article (référence / code-barres) et paiement (transaction).
    fireEvent.change(screen.getByLabelText('Référence article'), {
      target: { value: 'CIM-50' },
    });
    fireEvent.change(screen.getByLabelText('Référence de paiement'), {
      target: { value: 'OM-778' },
    });
    await waitFor(() => {
      const last = salesCalls().at(-1) ?? '';
      expect(last).toContain('article_reference=CIM-50');
      expect(last).toContain('payment_reference=OM-778');
    });
    const channel = screen.getByLabelText('Canal', { selector: 'input, select, span, div' });
    fireEvent.click(channel.closest('.p-dropdown') ?? channel);
    fireEvent.click(
      screen.getAllByRole('option', { name: 'Point de vente', hidden: true }).at(-1) as Element,
    );
    await waitFor(() => expect(salesCalls().at(-1)).toContain('channel=POS'));
    // Sans droit de consulter les articles ni les clients : pas de sélecteur correspondant.
    expect(screen.queryByLabelText('Article')).toBeNull();
  });

  it('sans la permission d’export : aucune action « Exporter »', async () => {
    renderWithCapabilities(<SalesPage />, { permissions: ['sales.sale.view'] });
    await screen.findByText('VENT-BOU-2026-000001');
    expect(screen.queryByRole('button', { name: 'Exporter' })).toBeNull();
  });

  it('UNE action « Exporter » : choix du format, mêmes filtres que la liste', async () => {
    const createObjectURL = vi.fn(() => 'blob:export');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    renderWithCapabilities(<SalesPage />, {
      permissions: ['sales.sale.view', 'sales.sale.export'],
    });
    await screen.findByText('VENT-BOU-2026-000001');
    expect(screen.getAllByRole('button', { name: 'Exporter' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Mes ventes' }));
    await waitFor(() => expect(salesCalls().at(-1)).toContain('mine=true'));

    fireEvent.click(screen.getByRole('button', { name: 'Exporter' }));
    const menu = await screen.findByRole('menu', { hidden: true });
    expect(within(menu).getByText('Excel (.xlsx)')).toBeTruthy();
    expect(within(menu).getByText('CSV (.csv)')).toBeTruthy();
    expect(within(menu).getByText('PDF (.pdf)')).toBeTruthy();
    fireEvent.click(within(menu).getByText('CSV (.csv)'));
    await waitFor(() => expect(click).toHaveBeenCalled());
    const exportUrl = fetchMock.mock.calls
      .map(([u]) => String(u))
      .find((u) => u.includes('/sales/export?')) as string;
    expect(exportUrl).toContain('format=csv');
    expect(exportUrl).toContain('mine=true');
    expect(exportUrl).toContain('sort=-created_at');
    expect(exportUrl).not.toContain('limit=');
    expect(createObjectURL).toHaveBeenCalled();
    click.mockRestore();
  });
});
