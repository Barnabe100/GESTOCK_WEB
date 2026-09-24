// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';

import ReceivablesPage from './ReceivablesPage';
import { receivable, VIEW } from './testData';

const navigate = vi.fn();
vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useNavigate: () => navigate,
}));

const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s/g, ' ');
const text = (el: HTMLElement) => (el.textContent ?? '').replace(/\s/g, ' ');

const SUMMARY = { total_receivables: '90000.00', receivables_count: 2, debtor_customers_count: 1 };

describe('liste des créances', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const calls = (part: string) =>
    fetchMock.mock.calls.map(([url]) => String(url)).filter((u) => u.includes(part));

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/receivables/summary')
        ? jsonResponse(SUMMARY)
        : pageOf([
            receivable(),
            receivable({
              sale_id: 'v2',
              sale_number: 'VTE-000002',
              site_id: 's2',
              site_name: 'Dépôt',
              customer_id: null,
              customer_code: null,
              customer_name: null,
              customer_is_active: null,
              total: '30000.00',
              paid_amount: '0.00',
              remaining_amount: '30000.00',
              payment_status: 'UNPAID',
            }),
          ]),
    );
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    navigate.mockReset();
    vi.unstubAllGlobals();
  });

  it('indicateurs, montants XOF, états traduits, sans code technique', async () => {
    renderWithCapabilities(<ReceivablesPage />, { permissions: VIEW });
    expect(await screen.findByText('VTE-000001')).toBeTruthy();
    const metrics = screen.getByRole('group', { name: 'Indicateurs des créances' });
    await waitFor(() => expect(text(metrics)).toContain(money('90000.00')));
    expect(text(metrics)).toContain('Total dû');
    expect(text(metrics)).toContain('Clients débiteurs');
    const row = screen.getByText('VTE-000001').closest('tr') as HTMLElement;
    expect(within(row).getByText('Awa Traoré')).toBeTruthy();
    expect(text(row)).toContain(money('100000.00'));
    expect(text(row)).toContain(money('40000.00'));
    expect(text(row)).toContain(money('60000.00'));
    expect(within(row).getByText('Partiellement payée')).toBeTruthy();
    const anonymous = screen.getByText('VTE-000002').closest('tr') as HTMLElement;
    expect(within(anonymous).getByText('Sans client')).toBeTruthy();
    expect(within(anonymous).getByText('Non payée')).toBeTruthy();
    expect(within(anonymous).getByText('Dépôt')).toBeTruthy(); // plusieurs sites : colonne Site
    expect(document.body.textContent).not.toMatch(/UNPAID|PARTIALLY_PAID|VALIDATED/);
  });

  it('filtres serveur (recherche, état, solde minimal) appliqués à la liste et aux indicateurs', async () => {
    renderWithCapabilities(<ReceivablesPage />, { permissions: VIEW });
    await screen.findByText('VTE-000001');
    const reset = screen.getByRole('button', { name: 'Réinitialiser' }) as HTMLButtonElement;
    expect(reset.disabled).toBe(true);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Awa' } });
    await waitFor(() => expect(calls('/receivables?').at(-1)).toContain('search=Awa'));
    expect(calls('/receivables/summary?').at(-1)).toContain('search=Awa');
    fireEvent.change(screen.getByLabelText('Solde dû minimal'), { target: { value: '50 000' } });
    await waitFor(() => expect(calls('/receivables?').at(-1)).toContain('min_amount=50000'));
    expect(calls('/receivables/summary?').at(-1)).toContain('min_amount=50000');
    expect(reset.disabled).toBe(false);
    fireEvent.click(reset);
    await waitFor(() => expect(calls('/receivables?').at(-1)).not.toContain('search='));
    expect(calls('/receivables?').at(-1)).not.toContain('min_amount=');
  });

  it('pagination et tri côté serveur', async () => {
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/receivables/summary')
        ? jsonResponse(SUMMARY)
        : jsonResponse({ items: [receivable()], total: 60, limit: 25, offset: 0 }),
    );
    renderWithCapabilities(<ReceivablesPage />, { permissions: VIEW });
    await screen.findByText('VTE-000001');
    expect(calls('/receivables?').at(-1)).toContain('limit=25');
    expect(calls('/receivables?').at(-1)).toContain('offset=0');
    fireEvent.click(screen.getByRole('button', { name: /next page|suivante/i }));
    await waitFor(() => expect(calls('/receivables?').at(-1)).toContain('offset=25'));
    fireEvent.click(screen.getByText('Solde dû'));
    await waitFor(() => expect(calls('/receivables?').at(-1)).toContain('sort=remaining_amount'));
  });

  it('ouvre la fiche de la vente (articles, paiements, solde dû)', async () => {
    renderWithCapabilities(<ReceivablesPage />, { permissions: VIEW });
    const row = (await screen.findByText('VTE-000001')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Ouvrir la vente' }));
    expect(navigate).toHaveBeenCalledWith('/sales/v1');
  });

  it('sans droit de consulter les ventes : pas de lien vers la fiche', async () => {
    renderWithCapabilities(<ReceivablesPage />, { permissions: ['receivables.receivable.view'] });
    const row = (await screen.findByText('VTE-000001')).closest('tr') as HTMLElement;
    expect(within(row).queryByRole('button', { name: 'Ouvrir la vente' })).toBeNull();
    fireEvent.click(row);
    expect(navigate).not.toHaveBeenCalled();
  });

  it('état vide explicite', async () => {
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/receivables/summary')
        ? jsonResponse({
            total_receivables: '0.00',
            receivables_count: 0,
            debtor_customers_count: 0,
          })
        : pageOf([]),
    );
    renderWithCapabilities(<ReceivablesPage />, { permissions: VIEW });
    expect(
      await screen.findByText('Aucune créance ouverte : toutes les ventes validées sont payées.'),
    ).toBeTruthy();
  });
});
