// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import JournalPage from './JournalPage';
import RegistersPage from './RegistersPage';
import SessionPage from './SessionPage';
import { cashSession, MANAGER, movement, register, SELLER, VIEWER } from './testData';

const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s/g, ' ');
const text = (el: Element) => (el.textContent ?? '').replace(/\s/g, ' ');

function withToast(element: ReactNode, show: ReturnType<typeof vi.fn>) {
  const ref = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const fetchMock = vi.fn<typeof fetch>();
const show = vi.fn();
const calls = (part: string) =>
  fetchMock.mock.calls.map(([url]) => String(url)).filter((u) => u.includes(part));
const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');

beforeEach(() => vi.stubGlobal('fetch', fetchMock));
afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  show.mockReset();
  vi.unstubAllGlobals();
});

describe('caisses', () => {
  const REGISTERS = [
    register(),
    register({ id: 'r2', code: 'CAI-002', name: 'Caisse secondaire', current_session: null }),
    register({
      id: 'r3',
      code: 'CAI-003',
      name: 'Ancienne caisse',
      is_active: false,
      current_session: null,
    }),
  ];

  it('statut, caissier et solde théorique ; actions selon l’état', async () => {
    fetchMock.mockImplementation(async () => pageOf(REGISTERS));
    renderWithCapabilities(withToast(<RegistersPage />, show), { permissions: SELLER });
    const open = (await screen.findByText('Caisse principale')).closest('tr') as HTMLElement;
    expect(within(open).getByText('Ouverte')).toBeTruthy();
    expect(within(open).getByText('Aïcha')).toBeTruthy();
    expect(text(open)).toContain(money('155000'));
    expect(within(open).getByRole('button', { name: 'Voir la session' })).toBeTruthy();
    expect(within(open).getByRole('button', { name: 'Clôturer la caisse' })).toBeTruthy();
    expect(within(open).queryByRole('button', { name: 'Ouvrir la caisse' })).toBeNull();
    const closed = screen.getByText('Caisse secondaire').closest('tr') as HTMLElement;
    expect(within(closed).getByText('Fermée')).toBeTruthy();
    expect(within(closed).getByRole('button', { name: 'Ouvrir la caisse' })).toBeTruthy();
    // Caisse désactivée : jamais ouvrable.
    const inactive = screen.getByText('Ancienne caisse').closest('tr') as HTMLElement;
    expect(within(inactive).getByText('Inactif')).toBeTruthy();
    expect(within(inactive).queryByRole('button', { name: 'Ouvrir la caisse' })).toBeNull();
    // Vendeur : aucune gestion des caisses.
    expect(screen.queryByRole('button', { name: 'Nouvelle caisse' })).toBeNull();
    expect(document.body.textContent).not.toMatch(/\bOPEN\b|\bCLOSED\b/);
  });

  it('ouverture : fond initial seul, le serveur fixe utilisateur et heure', async () => {
    fetchMock.mockImplementation(async (_url, init) =>
      init?.method === 'POST'
        ? jsonResponse(cashSession({ opening_float: '50000.00' }), 201)
        : pageOf(REGISTERS),
    );
    renderWithCapabilities(withToast(<RegistersPage />, show), { permissions: SELLER });
    const row = (await screen.findByText('Caisse secondaire')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Ouvrir la caisse' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toContain('Boutique');
    expect(dialog.textContent).toContain('enregistrés par le serveur');
    const amount = within(dialog).getByLabelText(/Fond initial/);
    fireEvent.change(amount, { target: { value: 'abc' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ouvrir la caisse' }));
    expect(await within(dialog).findByText(/Montant invalide/)).toBeTruthy();
    expect(posts()).toHaveLength(0);
    fireEvent.change(amount, { target: { value: '50 000' } });
    await act(async () => {
      within(dialog).getByRole('button', { name: 'Ouvrir la caisse' }).click();
    });
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(JSON.parse(String(posts()[0]?.[1]?.body))).toEqual({
      cash_register_id: 'r2',
      opening_float: '50000',
    });
  });

  it('gestionnaire : création et filtres serveur', async () => {
    fetchMock.mockImplementation(async () => pageOf(REGISTERS));
    renderWithCapabilities(withToast(<RegistersPage />, show), { permissions: MANAGER });
    await screen.findByText('Caisse principale');
    expect(screen.getAllByRole('button', { name: 'Nouvelle caisse' }).length).toBeGreaterThan(0);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'CAI-2' } });
    await waitFor(() => expect(calls('/cash/registers?').at(-1)).toContain('search=CAI-2'));
  });
});

describe('session de caisse', () => {
  const JOURNAL = [
    movement({
      id: 'm0',
      movement_type: 'OPENING_FLOAT',
      amount: '100000.00',
      signed_amount: '100000.00',
      reference: null,
      source_type: null,
      source_id: null,
      source_number: null,
      payment_id: null,
      balance_after: '100000.00',
    }),
    movement(),
    movement({
      id: 'm2',
      movement_type: 'MANUAL_CASH_OUT',
      amount: '20000.00',
      signed_amount: '-20000.00',
      category: 'EXPENSE',
      reason: 'Carburant',
      reference: null,
      source_type: null,
      source_id: null,
      source_number: null,
      payment_id: null,
      balance_after: '130000.00',
    }),
  ];
  let current = cashSession();

  beforeEach(() => {
    current = cashSession();
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'POST' && u.endsWith('/close')) {
        return jsonResponse(
          cashSession({
            status: 'CLOSED',
            counted_balance: '153500.00',
            variance: '-1500.00',
          }),
        );
      }
      if (u.includes('/movements')) return pageOf(JOURNAL);
      return jsonResponse(current);
    });
  });

  const render = (permissions = MANAGER, route = '/cash/sessions/cs1') =>
    renderWithCapabilities(withToast(<SessionPage />, show), {
      permissions,
      path: '/cash/sessions/:id',
      route,
    });

  it('résumé et journal : entrées, sorties, solde après chaque mouvement', async () => {
    render();
    const summary = await screen.findByRole('group', { name: 'Résumé de la session' });
    expect(text(summary)).toContain(`${money('100000')}Fond initial`);
    expect(text(summary)).toContain(`${money('155000')}Solde théorique`);
    expect(text(summary)).toContain(`${money('70000')}Sorties`);
    const sale = (await screen.findByText('Encaissement vente')).closest('tr') as HTMLElement;
    expect(within(sale).getByRole('link', { name: 'VTE-000001 · PAY-000001' })).toBeTruthy();
    expect(text(sale)).toContain(money('150000'));
    const out = screen
      .getByText('Sortie de caisse', { selector: '.sm-badge' })
      .closest('tr') as HTMLElement;
    expect(within(out).getByText('Dépense — Carburant')).toBeTruthy();
    expect(text(out)).toContain(money('130000'));
    expect(screen.getByText('Fond initial', { selector: '.sm-badge' })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/SALE_CASH_IN|MANUAL_CASH_OUT|OPENING_FLOAT/);
  });

  it('clôture : récapitulatif, écart indicatif, confirmation explicite, écart non envoyé', async () => {
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Clôturer la caisse' }));
    const dialog = await screen.findByRole('dialog');
    expect(text(within(dialog).getByTestId('close-theoretical'))).toBe(money('155000'));
    const submit = within(dialog).getByRole('button', { name: 'Clôturer la caisse' });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/Montant compté/), {
      target: { value: '153500' },
    });
    const variance = within(dialog).getByTestId('close-variance');
    expect(text(variance)).toContain(money('-1500'));
    expect(text(variance)).toContain('Manquant');
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(dialog).getByLabelText(/Je confirme/));
    await act(async () => {
      submit.click();
    });
    await waitFor(() => expect(posts()).toHaveLength(1));
    const body = JSON.parse(String(posts()[0]?.[1]?.body)) as Record<string, unknown>;
    expect(body).toEqual({ counted_balance: '153500', note: null });
  });

  it('sortie refusée par le serveur : solde disponible affiché', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'POST') {
        return jsonResponse(
          { code: 'cash_insufficient_balance', status: 422, balance: '155000.00' },
          422,
        );
      }
      if (u.includes('/movements')) return pageOf(JOURNAL);
      return jsonResponse(current);
    });
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Sortie de caisse' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/^Montant/), { target: { value: '200000' } });
    fireEvent.change(within(dialog).getByLabelText(/^Motif/), {
      target: { value: 'Remise en banque' },
    });
    await act(async () => {
      within(dialog).getByRole('button', { name: 'Sortie de caisse' }).click();
    });
    expect(
      await within(dialog).findByText(
        new RegExp(`Solde de caisse insuffisant.*${money('155000').replace(/\s/g, '\\s')}`),
      ),
    ).toBeTruthy();
    const body = JSON.parse(String(posts()[0]?.[1]?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      movement_type: 'MANUAL_CASH_OUT',
      amount: '200000',
      category: 'EXPENSE',
      reason: 'Remise en banque',
    });
    expect(typeof body.idempotency_key).toBe('string');
  });

  it('session fermée : lecture seule ; écart affiché', async () => {
    current = cashSession({
      status: 'CLOSED',
      closed_at: '2026-09-25T19:00:00Z',
      closed_by_name: 'Aïcha',
      counted_balance: '153500.00',
      variance: '-1500.00',
    });
    render();
    expect(await screen.findByText('Fermée')).toBeTruthy();
    expect(screen.getByText(/Session clôturée : consultable/)).toBeTruthy();
    expect(screen.getAllByText('Manquant').length).toBeGreaterThan(0);
    for (const name of ['Clôturer la caisse', 'Entrée de caisse', 'Sortie de caisse']) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
  });

  it('consultant : aucune action', async () => {
    render(VIEWER);
    await screen.findByRole('group', { name: 'Résumé de la session' });
    for (const name of ['Clôturer la caisse', 'Entrée de caisse', 'Sortie de caisse']) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
  });
});

describe('journal de caisse', () => {
  it('toutes caisses, filtres et pagination serveur', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse({ items: [movement()], total: 60, limit: 25, offset: 0 }),
    );
    renderWithCapabilities(withToast(<JournalPage />, show), { permissions: MANAGER });
    const row = (await screen.findByText('Encaissement vente')).closest('tr') as HTMLElement;
    expect(within(row).getByRole('link', { name: 'Caisse principale · SES-000001' })).toBeTruthy();
    expect(calls('/cash/movements?').at(-1)).toContain('sort=-occurred_at');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'VTE-1' } });
    await waitFor(() => expect(calls('/cash/movements?').at(-1)).toContain('search=VTE-1'));
    fireEvent.change(screen.getByLabelText('Montant minimal'), { target: { value: '1 000' } });
    await waitFor(() => expect(calls('/cash/movements?').at(-1)).toContain('min_amount=1000'));
    fireEvent.click(screen.getByRole('button', { name: /next page|suivante/i }));
    await waitFor(() => expect(calls('/cash/movements?').at(-1)).toContain('offset=25'));
  });
});
