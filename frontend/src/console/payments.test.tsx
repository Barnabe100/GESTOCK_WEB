// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { RefObject } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '@/core/i18n';
import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { ConsoleAuthProvider } from './ConsoleAuth';
import { consoleRoutes } from './router';
import type { ConsolePayment } from './types';

const ADMIN = { id: 'a1', email: 'admin@technova.example', full_name: 'Awa Admin' };
const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s/g, ' ');

const PENDING: ConsolePayment = {
  id: 'p-1',
  tenant_id: 't-1',
  tenant_name: 'ABC Commerce',
  subscription_id: 's-1',
  plan_code: 'STANDARD',
  amount: '10000.00',
  currency: 'XOF',
  period_start: '2026-10-01',
  period_end: '2026-11-01',
  payment_method: 'BANK_TRANSFER',
  declared_reference: 'VIR-001',
  status: 'PENDING',
  created_at: '2026-09-25T10:00:00Z',
  decided_at: null,
  decided_by_email: null,
  rejection_reason: null,
};

const REJECTED: ConsolePayment = {
  ...PENDING,
  id: 'p-2',
  declared_reference: 'OM-42',
  payment_method: 'MOBILE_MONEY',
  status: 'REJECTED',
  decided_at: '2026-09-26T08:00:00Z',
  decided_by_email: 'admin@technova.example',
  rejection_reason: 'Référence introuvable',
};

const fetchMock = vi.fn<typeof fetch>();
const show = vi.fn();

function renderConsole(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(consoleRoutes, { initialEntries: [path] });
  const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastContext.Provider value={toast}>
        <ConsoleAuthProvider>
          <RouterProvider router={router} />
        </ConsoleAuthProvider>
      </ToastContext.Provider>
    </QueryClientProvider>,
  );
}

function consoleApi({
  detail = PENDING,
  decide,
}: {
  detail?: ConsolePayment | (() => ConsolePayment);
  decide?: (url: string, body: unknown) => Response | Promise<Response>;
} = {}) {
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (u.endsWith('/me')) return jsonResponse(ADMIN);
    if (init?.method === 'POST' && /\/payments\/p-\d\/(confirm|reject)$/.test(u)) {
      const body: unknown = JSON.parse(String(init.body));
      return decide ? decide(u, body) : jsonResponse({ ...PENDING, status: 'CONFIRMED' });
    }
    if (/\/payments\/p-\d$/.test(u)) {
      return jsonResponse(typeof detail === 'function' ? detail() : detail);
    }
    if (u.includes('/payments?')) {
      return jsonResponse({ items: [PENDING, REJECTED], total: 2, limit: 25, offset: 0 });
    }
    if (u.includes('/audit')) return jsonResponse({ items: [], total: 0, limit: 20, offset: 0 });
    return jsonResponse({ code: 'not_found' }, 404);
  });
}

const decisions = () =>
  fetchMock.mock.calls.filter(
    ([u, init]) => init?.method === 'POST' && /(confirm|reject)$/.test(String(u)),
  );
const urls = () => fetchMock.mock.calls.map(([u]) => String(u));

async function panelOption(label: string) {
  const panel = await waitFor(() => {
    const found = document.querySelector('.p-dropdown-panel');
    if (!found) throw new Error('panneau fermé');
    return found as HTMLElement;
  });
  return within(panel).getByText(label);
}

async function openDecision(name: string) {
  fireEvent.click(await screen.findByRole('button', { name }));
  return screen.findByRole('form', { name });
}

describe('Console TechNova : paiements', () => {
  beforeEach(() => vi.stubGlobal('fetch', fetchMock));
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  it('navigation et liste : entreprise, montant, période, moyen, référence, statut', async () => {
    consoleApi();
    renderConsole('/tech-admin/payments');
    const row = (await screen.findByText('VIR-001')).closest('tr') as HTMLElement;
    expect(within(row).getByText('ABC Commerce')).toBeTruthy();
    expect(within(row).getByText(money('10000.00'))).toBeTruthy();
    expect(within(row).getByText('Virement bancaire')).toBeTruthy();
    expect(within(row).getByText('En attente')).toBeTruthy();
    expect(within(row).getByText(/1 oct\. 2026 → 1 nov\. 2026/)).toBeTruthy();
    const rejected = screen.getByText('OM-42').closest('tr') as HTMLElement;
    expect(within(rejected).getByText('Rejeté')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Paiements' })).toBeTruthy();
    const first = urls().find((u) => u.includes('/payments?'));
    expect(first).toContain('sort=-created_at');
    expect(document.body.textContent).not.toMatch(/console:|subscriptionPayment/);
  });

  it('filtres : statut, référence et entreprise transmis au serveur', async () => {
    consoleApi();
    renderConsole('/tech-admin/payments?tenant_id=t-1');
    await screen.findByText('VIR-001');
    expect(urls().some((u) => u.includes('/payments?') && u.includes('tenant_id=t-1'))).toBe(true);
    fireEvent.click(
      within(screen.getByTestId('filter-payment-status')).getByRole('button', { hidden: true }),
    );
    fireEvent.click(await panelOption('En attente'));
    await waitFor(() => expect(urls().some((u) => u.includes('status=PENDING'))).toBe(true));
    fireEvent.change(screen.getByPlaceholderText('Rechercher une référence'), {
      target: { value: 'VIR' },
    });
    await waitFor(() => expect(urls().some((u) => u.includes('search=VIR'))).toBe(true));
    fireEvent.click(screen.getByTestId('tenant-filter'));
    await waitFor(() => expect(urls().at(-1)).not.toContain('tenant_id'));
  });

  it('paiement décidé : aucune action, motif de rejet et décideur affichés', async () => {
    consoleApi({ detail: REJECTED });
    renderConsole('/tech-admin/payments/p-2');
    expect((await screen.findByTestId('payment-rejection-reason')).textContent).toBe(
      'Référence introuvable',
    );
    expect(screen.getByText('admin@technova.example')).toBeTruthy();
    expect(screen.queryByTestId('payment-actions')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Confirmer le paiement' })).toBeNull();
    expect(screen.getByText(/Décision définitive/)).toBeTruthy();
  });

  it('confirmation : raison et case obligatoires, aucune activation annoncée', async () => {
    consoleApi();
    renderConsole('/tech-admin/payments/p-1');
    expect(await screen.findByText(/n'active pas l'abonnement/)).toBeTruthy();
    const form = await openDecision('Confirmer le paiement');
    const submit = within(form).getByRole('button', { name: 'Confirmer le paiement' });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(form).getByLabelText('Je confirme cette décision définitive.'));
    fireEvent.click(submit);
    expect(await within(form).findByText('La raison est obligatoire.')).toBeTruthy();
    expect(decisions()).toHaveLength(0);

    fireEvent.change(within(form).getByLabelText(/Raison/), {
      target: { value: '  Virement reçu  ' },
    });
    fireEvent.click(submit);
    await waitFor(() => expect(decisions()).toHaveLength(1));
    const [url, init] = decisions()[0] as [string, RequestInit];
    expect(url).toMatch(/\/payments\/p-1\/confirm$/);
    expect(JSON.parse(String(init.body))).toEqual({ reason: 'Virement reçu' });
    expect((init.headers as Record<string, string>)['X-TechNova-Console']).toBe('1');
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(expect.objectContaining({ summary: 'Paiement confirmé' })),
    );
  });

  it('rejet : motif obligatoire visible par l’entreprise, envoi unique', async () => {
    let release: (r: Response) => void = () => undefined;
    consoleApi({
      decide: () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    });
    renderConsole('/tech-admin/payments/p-1');
    const form = await openDecision('Rejeter le paiement');
    expect(within(form).getByText(/visible par l'entreprise/)).toBeTruthy();
    fireEvent.change(within(form).getByLabelText(/Motif du rejet/), {
      target: { value: 'Référence introuvable' },
    });
    fireEvent.click(within(form).getByLabelText('Je confirme cette décision définitive.'));
    const submit = within(form).getByRole('button', { name: 'Rejeter le paiement' });
    fireEvent.click(submit);
    await waitFor(() => expect(decisions()).toHaveLength(1));
    fireEvent.click(submit); // double clic pendant l'envoi
    fireEvent.submit(form);
    release(jsonResponse(REJECTED));
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(expect.objectContaining({ summary: 'Paiement rejeté' })),
    );
    expect(decisions()).toHaveLength(1);
    expect(String(decisions()[0]?.[0])).toMatch(/\/payments\/p-1\/reject$/);
  });

  it('409 : décision déjà prise ailleurs, message traduit et fiche relue', async () => {
    let decided = false;
    consoleApi({
      detail: () => (decided ? { ...PENDING, status: 'CONFIRMED' } : PENDING),
      decide: () => {
        decided = true;
        return jsonResponse(
          { code: 'payment_already_decided', detail: 'déjà décidé', status: 'CONFIRMED' },
          409,
        );
      },
    });
    renderConsole('/tech-admin/payments/p-1');
    const form = await openDecision('Confirmer le paiement');
    fireEvent.change(within(form).getByLabelText(/Raison/), { target: { value: 'Reçu' } });
    fireEvent.click(within(form).getByLabelText('Je confirme cette décision définitive.'));
    fireEvent.click(within(form).getByRole('button', { name: 'Confirmer le paiement' }));
    expect(await within(form).findByText(/déjà été décidé/)).toBeTruthy();
    expect(
      (within(form).getByRole('button', { name: 'Confirmer le paiement' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    // La fiche relue n'offre plus aucune action.
    await waitFor(() => expect(screen.queryByTestId('payment-actions')).toBeNull());
  });

  it('paiement inconnu : message traduit', async () => {
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.endsWith('/me')) return jsonResponse(ADMIN);
      return jsonResponse({ code: 'subscription_payment_not_found', detail: 'x' }, 404);
    });
    renderConsole('/tech-admin/payments/p-9');
    expect(await screen.findByText('Paiement introuvable.')).toBeTruthy();
  });
});
