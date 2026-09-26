// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import type { SubscriptionDetails, SubscriptionPayment } from './api';
import SubscriptionPage from './SubscriptionPage';

const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s/g, ' ');

const SUBSCRIPTION: SubscriptionDetails = {
  id: 'sub-1',
  plan_code: 'STANDARD',
  plan_name: 'Standard',
  billing_period: 'monthly',
  status: 'pending_activation',
  effective_status: 'pending_activation',
  started_at: '2026-09-01T00:00:00Z',
  current_period_start: '2026-09-01T00:00:00Z',
  current_period_end: '2026-09-01T00:00:00Z',
  grace_days: 7,
  limits: {},
  features: [],
  allowed_access: ['read', 'admin', 'billing'],
};

const payment = (over: Partial<SubscriptionPayment> = {}): SubscriptionPayment => ({
  id: 'pay-1',
  subscription_id: 'sub-1',
  amount: '10000.00',
  currency: 'XOF',
  period_start: '2026-10-01',
  period_end: '2026-11-01',
  payment_method: 'BANK_TRANSFER',
  declared_reference: 'VIR-001',
  declared_by: 'u-me',
  status: 'PENDING',
  created_at: '2026-09-25T10:00:00Z',
  decided_at: null,
  rejection_reason: null,
  ...over,
});

const PAYMENTS = [
  payment(),
  payment({
    id: 'pay-2',
    amount: '5000.00',
    payment_method: 'MOBILE_MONEY',
    declared_reference: 'OM-42',
    status: 'REJECTED',
    decided_at: '2026-09-26T08:00:00Z',
    rejection_reason: 'Référence introuvable',
  }),
  payment({ id: 'pay-3', declared_reference: 'VIR-000', status: 'CONFIRMED' }),
];

const fetchMock = vi.fn<typeof fetch>();
const show = vi.fn();

function api(onPost?: (body: Record<string, unknown>) => Response | Promise<Response>) {
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (init?.method === 'POST' && u.endsWith('/subscription/payments')) {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      return onPost ? onPost(body) : jsonResponse(payment({ id: 'pay-new' }), 201);
    }
    if (u.includes('/subscription/payments?')) {
      return jsonResponse({ items: PAYMENTS, total: 3, limit: 25, offset: 0 });
    }
    if (u.endsWith('/subscription')) return jsonResponse(SUBSCRIPTION);
    return jsonResponse({ code: 'not_found' }, 404);
  });
}

function renderPage(permissions: string[]) {
  const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return renderWithCapabilities(
    <ToastContext.Provider value={toast}>
      <SubscriptionPage />
    </ToastContext.Provider>,
    { permissions },
  );
}

const VIEW = ['subscription.subscription.view'];
const DECLARE = [...VIEW, 'subscription.payment.declare'];
const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');

async function openForm() {
  fireEvent.click(await screen.findByRole('button', { name: 'Déclarer un paiement' }));
  return screen.findByRole('form', { name: 'Déclarer un paiement à TechNova' });
}

function fill(form: HTMLElement, values: Record<string, string>) {
  const fields: Record<string, string> = {
    amount: 'Montant',
    start: 'Début de la période couverte',
    end: 'Fin de la période couverte',
    reference: 'Référence',
  };
  for (const [key, value] of Object.entries(values)) {
    fireEvent.change(within(form).getByLabelText(fields[key] as string, { exact: false }), {
      target: { value },
    });
  }
}

describe('Abonnement : paiements déclarés à TechNova', () => {
  beforeEach(() => vi.stubGlobal('fetch', fetchMock));
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  it('liste : statut, montant, période, moyen, référence et motif de rejet', async () => {
    api();
    renderPage(VIEW);
    const pending = (await screen.findByText('VIR-001')).closest('tr') as HTMLElement;
    expect(within(pending).getByText('En attente')).toBeTruthy();
    expect(within(pending).getByText(money('10000.00'))).toBeTruthy();
    expect(within(pending).getByText('Virement bancaire')).toBeTruthy();
    expect(within(pending).getByText(/du .*1 oct\. 2026.* au .*1 nov\. 2026/)).toBeTruthy();
    const rejected = screen.getByText('OM-42').closest('tr') as HTMLElement;
    expect(within(rejected).getByText('Rejeté')).toBeTruthy();
    expect(within(rejected).getByText('Mobile money')).toBeTruthy();
    expect(within(rejected).getByTestId('rejection-reason').textContent).toBe(
      'Motif du rejet : Référence introuvable',
    );
    const confirmed = screen.getByText('VIR-000').closest('tr') as HTMLElement;
    expect(within(confirmed).getByText('Confirmé')).toBeTruthy();
    // Aucune clé de traduction brute affichée.
    expect(document.body.textContent).not.toMatch(/subscriptionPayment|subscriptionPayments\./);
  });

  it('sans la permission de déclarer : lecture seule', async () => {
    api();
    renderPage(VIEW);
    await screen.findByText('VIR-001');
    expect(screen.queryByRole('button', { name: 'Déclarer un paiement' })).toBeNull();
  });

  it('filtre par statut transmis au serveur', async () => {
    api();
    renderPage(VIEW);
    await screen.findByText('VIR-001');
    const filter = screen.getByTestId('subscription-payment-status-filter');
    fireEvent.click(within(filter).getByRole('button', { hidden: true }));
    const panel = await waitFor(() => {
      const found = document.querySelector('.p-dropdown-panel');
      if (!found) throw new Error('panneau fermé');
      return found as HTMLElement;
    });
    fireEvent.click(within(panel).getByText('Rejeté'));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes('status=REJECTED'))).toBe(true),
    );
  });

  it('formulaire : validation avant envoi (montant, dates, référence)', async () => {
    api();
    renderPage(DECLARE);
    const form = await openForm();
    fireEvent.click(within(form).getByRole('button', { name: 'Déclarer le paiement' }));
    expect(await within(form).findByText(/Montant invalide/)).toBeTruthy();
    expect(within(form).getAllByText(/Champ obligatoire/).length).toBeGreaterThan(0);

    fill(form, { amount: '10,001', start: '2026-10-01', end: '2026-09-01', reference: 'X' });
    fireEvent.click(within(form).getByRole('button', { name: 'Déclarer le paiement' }));
    expect(await within(form).findByText('La fin de période doit suivre son début.')).toBeTruthy();
    expect(within(form).getByText(/Montant invalide/)).toBeTruthy();
    expect(posts()).toHaveLength(0);
  });

  it('déclaration : aucun champ de décision envoyé, clé d’idempotence, envoi unique', async () => {
    let release: (r: Response) => void = () => undefined;
    const bodies: Record<string, unknown>[] = [];
    api((body) => {
      bodies.push(body);
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    });
    renderPage(DECLARE);
    const form = await openForm();
    fill(form, {
      amount: '10 000,50',
      start: '2026-10-01',
      end: '2026-11-01',
      reference: '  VIR-2026-9  ',
    });
    const submit = within(form).getByRole('button', { name: 'Déclarer le paiement' });
    fireEvent.click(submit);
    await waitFor(() => expect(bodies).toHaveLength(1));
    fireEvent.click(submit); // double clic pendant l'envoi : ignoré
    fireEvent.submit(form);
    release(jsonResponse(payment({ id: 'pay-new' }), 201));
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({
          summary: 'Paiement déclaré : en attente de vérification par TechNova.',
        }),
      ),
    );
    expect(bodies).toHaveLength(1);
    const body = bodies[0] as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual([
      'amount',
      'declared_reference',
      'idempotency_key',
      'payment_method',
      'period_end',
      'period_start',
      'subscription_id',
    ]);
    expect(body).toMatchObject({
      subscription_id: 'sub-1',
      amount: '10000.50',
      period_start: '2026-10-01',
      period_end: '2026-11-01',
      payment_method: 'BANK_TRANSFER',
      declared_reference: 'VIR-2026-9',
    });
    expect(body.idempotency_key).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('refus du serveur : message traduit, même clé au nouvel envoi', async () => {
    const keys: unknown[] = [];
    api((body) => {
      keys.push(body.idempotency_key);
      return jsonResponse(
        { code: 'period_too_long', detail: 'Période trop longue', max_months: 24 },
        422,
      );
    });
    renderPage(DECLARE);
    const form = await openForm();
    fill(form, { amount: '1000', start: '2026-10-01', end: '2029-10-01', reference: 'R' });
    fireEvent.click(within(form).getByRole('button', { name: 'Déclarer le paiement' }));
    expect(await within(form).findByText('Période trop longue (24 mois au plus).')).toBeTruthy();
    fireEvent.click(within(form).getByRole('button', { name: 'Déclarer le paiement' }));
    await waitFor(() => expect(keys).toHaveLength(2));
    expect(keys[0]).toBe(keys[1]);
  });
});
