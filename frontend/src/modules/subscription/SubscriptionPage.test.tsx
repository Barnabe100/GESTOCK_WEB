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
const text = (el: HTMLElement) => (el.textContent ?? '').replace(/\s/g, ' ');

const SUBSCRIPTION: SubscriptionDetails = {
  id: 'sub-1',
  site: { id: 'site-1', name: 'Boutique', code: 'BTQ' },
  requested_activations: 2,
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
  license: null,
  effective_plan: { code: 'STANDARD', name: 'Standard' },
  next_plan: null,
  renewal: {
    subscription_id: 'sub-1',
    site_id: 'site-1',
    kind: 'initial',
    plan: { code: 'STANDARD', name: 'Standard' },
    billing_period: 'monthly',
    valid_from: '2026-10-01',
    valid_until: '2026-10-31',
    activations: 2,
    current_activations: null,
    activations_explicit: false,
    amount: null,
    currency: 'XOF',
    coverage_end: null,
    grace_continuity: false,
    renewal_due: false,
  },
};

type Quote = SubscriptionDetails['renewal'];
/** Devis calculé par le serveur : postes demandés pris en compte, tarif éventuel. */
const quoteFor = (base: Quote, requested: number | null, unit: string | null): Quote => ({
  ...base,
  activations: requested ?? base.activations,
  activations_explicit: requested !== null && requested !== base.activations,
  amount:
    unit === null
      ? null
      : (10000 + Math.max(0, (requested ?? base.activations) - 1) * Number(unit)).toFixed(2),
});

const payment = (over: Partial<SubscriptionPayment> = {}): SubscriptionPayment => ({
  id: 'pay-1',
  subscription_id: 'sub-1',
  amount: '10000.00',
  currency: 'XOF',
  period_start: '2026-10-01',
  period_end: '2026-11-01',
  payment_method: 'BANK_TRANSFER',
  declared_reference: 'VIR-001',
  requested_activations: null,
  declared_by: 'u-me',
  status: 'PENDING',
  created_at: '2026-09-25T10:00:00Z',
  decided_at: null,
  rejection_reason: null,
  ...over,
});

const POSTE = {
  id: 'act-1',
  site_id: 'site-1',
  subscription_id: 'sub-1',
  license_id: 'lic-1',
  installation_id: '8f6b2c1e-0000-4000-8000-000000000001',
  label: 'Caisse 1',
  client_version: '1.0.0',
  status: 'ACTIVE',
  activated_at: '2026-09-26T11:00:00Z',
  last_seen_at: '2026-09-26T12:00:00Z',
  stale: true,
  released_at: null,
  release_source: null,
  release_reason: null,
};

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

function api(
  onPost?: (body: Record<string, unknown>) => Response | Promise<Response>,
  {
    subscription = SUBSCRIPTION,
    unit = null as string | null,
    notifications = [] as unknown[],
  } = {},
) {
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (init?.method === 'POST' && u.endsWith('/subscription/payments')) {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      return onPost ? onPost(body) : jsonResponse(payment({ id: 'pay-new' }), 201);
    }
    if (u.includes('/subscription/payments?')) {
      return jsonResponse({ items: PAYMENTS, total: 3, limit: 25, offset: 0 });
    }
    if (u.includes('/renewal-quote')) {
      const requested = new URL(u, 'http://x').searchParams.get('requested_activations');
      return jsonResponse(
        quoteFor(subscription.renewal, requested ? Number(requested) : null, unit),
      );
    }
    if (u.includes('/notifications?')) {
      return jsonResponse({
        items: notifications,
        total: notifications.length,
        limit: 5,
        offset: 0,
      });
    }
    if (init?.method === 'POST' && u.includes('/notifications/')) {
      return new Response(null, { status: 204 });
    }
    if (u.endsWith('/subscriptions')) return jsonResponse([subscription]);
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
  const fields: Record<string, string> = { amount: 'Montant', reference: 'Référence' };
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

  it('licence du site : numéro, état, validité et postes autorisés (lecture seule)', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (u.endsWith('/subscriptions')) {
        return jsonResponse([
          {
            ...SUBSCRIPTION,
            status: 'active',
            effective_status: 'active',
            license: {
              id: 'lic-1',
              license_number: 'LIC-2026-00007',
              license_version: 1,
              state: 'ACTIVE',
              plan_code: 'STANDARD',
              valid_from: '2026-10-01',
              valid_until: '2027-09-30',
              max_activations: 3,
              activations_used: 1,
              activations_available: 2,
              issued_at: '2026-09-26T10:00:00Z',
              revoked_at: null,
            },
          },
        ]);
      }
      if (u.includes('/subscription/payments?')) {
        return jsonResponse({ items: [], total: 0, limit: 25, offset: 0 });
      }
      if (u.includes('/license-activations?site_id=site-1')) {
        return jsonResponse({ items: [POSTE], total: 1, limit: 100, offset: 0 });
      }
      if (init?.method === 'POST' && u.endsWith('/license-activations/act-1/release')) {
        return jsonResponse({ ...POSTE, status: 'RELEASED' });
      }
      return jsonResponse({ code: 'not_found' }, 404);
    });
    renderPage([...VIEW, 'subscription.activation.manage']);
    const license = await screen.findByTestId('license');
    expect(license.textContent).toContain('N° LIC-2026-00007');
    expect(license.textContent).toContain('Active');
    expect(license.textContent).toContain('Valide du 1 oct. 2026 au 30 sept. 2027');
    expect((await screen.findByTestId('license-activations')).textContent).toBe(
      '3 postes autorisés · 1 utilisé · 2 disponibles',
    );
    // Postes du site : liste et libération (raison obligatoire, confirmation) ; aucune
    // activation depuis le Web.
    expect(await screen.findByText('Caisse 1')).toBeTruthy();
    expect(screen.getByText('Non vu récemment')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Activer/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Libérer Caisse 1' }));
    const form = await screen.findByRole('form', { name: 'Libérer le poste « Caisse 1 »' });
    fireEvent.change(within(form).getByLabelText(/Raison/), { target: { value: 'Remplacé' } });
    fireEvent.click(within(form).getByLabelText('Je confirme la libération de ce poste.'));
    fireEvent.click(within(form).getByRole('button', { name: 'Libérer' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]![0]).toMatch(/\/license-activations\/act-1\/release$/);
    expect(JSON.parse(String(posts()[0]![1]?.body))).toEqual({ reason: 'Remplacé' });
    expect(screen.queryByRole('button', { name: /licence/i })).toBeNull();
  });

  it('sans licence : le site attend le paiement confirmé et la licence', async () => {
    api();
    renderPage(VIEW);
    expect((await screen.findByTestId('license-none')).textContent).toContain('Aucune licence');
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

  it('formulaire : période et postes calculés par le serveur, validation avant envoi', async () => {
    api();
    renderPage(DECLARE);
    const form = await openForm();
    // Période et postes affichés, jamais saisis.
    const quote = await within(form).findByTestId('renewal-quote');
    expect(quote.textContent).toContain('du 1 oct. 2026 au 31 oct. 2026');
    expect(within(form).getByTestId('renewal-postes').textContent).toBe('2 postes');
    expect(within(form).queryByLabelText(/Début de la période/)).toBeNull();
    // Offre sans tarif : le montant convenu est demandé.
    fireEvent.click(within(form).getByRole('button', { name: 'Déclarer le paiement' }));
    expect(await within(form).findByText(/Champ obligatoire/)).toBeTruthy();
    fill(form, { amount: '10,001', reference: 'X' });
    fireEvent.click(within(form).getByRole('button', { name: 'Déclarer le paiement' }));
    expect(await within(form).findByText(/Montant invalide/)).toBeTruthy();
    expect(posts()).toHaveLength(0);
  });

  it('déclaration : ni période ni décision envoyées, clé d’idempotence, envoi unique', async () => {
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
    await within(form).findByTestId('renewal-quote');
    fill(form, { amount: '10 000,50', reference: '  VIR-2026-9  ' });
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
      'subscription_id',
    ]);
    expect(body).toMatchObject({
      subscription_id: 'sub-1',
      amount: '10000.50',
      payment_method: 'BANK_TRANSFER',
      declared_reference: 'VIR-2026-9',
    });
    expect(body.idempotency_key).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('offre tarifée : montant du serveur, postes demandés explicitement', async () => {
    const bodies: Record<string, unknown>[] = [];
    api(
      (body) => {
        bodies.push(body);
        return jsonResponse(payment({ id: 'pay-new' }), 201);
      },
      { unit: '3000' },
    );
    renderPage(DECLARE);
    const form = await openForm();
    expect(text(await within(form).findByTestId('renewal-amount'))).toBe(money('13000.00'));
    expect(within(form).queryByLabelText(/^Montant/)).toBeNull();
    fireEvent.click(within(form).getByLabelText('Demander un autre nombre de postes'));
    const input = within(form).getByLabelText(/Nombre de postes souhaité/);
    fireEvent.change(input, { target: { value: '5' } });
    fireEvent.blur(input);
    await waitFor(() =>
      expect(text(within(form).getByTestId('renewal-amount'))).toBe(money('22000.00')),
    );
    expect(within(form).getByTestId('renewal-postes').textContent).toContain('5 postes');
    expect(within(form).getByTestId('renewal-postes').textContent).toContain('à confirmer');
    fill(form, { reference: 'OM-7' });
    fireEvent.click(within(form).getByRole('button', { name: 'Déclarer le paiement' }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).not.toHaveProperty('amount');
    expect(bodies[0]).toMatchObject({ requested_activations: 5, declared_reference: 'OM-7' });
  });

  it('refus du serveur : message traduit, même clé au nouvel envoi', async () => {
    const keys: unknown[] = [];
    api((body) => {
      keys.push(body.idempotency_key);
      return jsonResponse({ code: 'amount_computed_by_server', detail: 'x' }, 422);
    });
    renderPage(DECLARE);
    const form = await openForm();
    await within(form).findByTestId('renewal-quote');
    fill(form, { amount: '1000', reference: 'R' });
    fireEvent.click(within(form).getByRole('button', { name: 'Déclarer le paiement' }));
    expect(await within(form).findByText(/calculé par le serveur/)).toBeTruthy();
    fireEvent.click(within(form).getByRole('button', { name: 'Déclarer le paiement' }));
    await waitFor(() => expect(keys).toHaveLength(2));
    expect(keys[0]).toBe(keys[1]);
  });

  it('R4 : offre en vigueur, offre au prochain renouvellement, « Renouveler »', async () => {
    const renewing: SubscriptionDetails = {
      ...SUBSCRIPTION,
      status: 'active',
      effective_status: 'past_due',
      plan_code: 'ENTREPRISE',
      plan_name: 'Entreprise',
      next_plan: { code: 'ENTREPRISE', name: 'Entreprise' },
      renewal: {
        ...SUBSCRIPTION.renewal,
        kind: 'renewal',
        plan: { code: 'ENTREPRISE', name: 'Entreprise' },
        valid_from: '2026-09-28',
        valid_until: '2026-10-27',
        activations: 3,
        current_activations: 3,
        renewal_due: true,
        grace_continuity: true,
      },
    };
    api(undefined, { subscription: renewing });
    renderPage(DECLARE);
    expect((await screen.findByTestId('effective-plan')).textContent).toBe('Standard');
    expect(screen.getByTestId('next-plan').textContent).toBe(
      'Au prochain renouvellement : Entreprise',
    );
    const next = screen.getByTestId('next-period');
    expect(next.textContent).toContain('du 28 sept. 2026 au 27 oct. 2026 · 3 postes');
    expect(next.textContent).toContain('sans perte de jours');
    fireEvent.click(within(next).getByRole('button', { name: 'Renouveler' }));
    const form = await screen.findByRole('form', { name: 'Déclarer un paiement à TechNova' });
    expect((await within(form).findByTestId('renewal-postes')).textContent).toBe('3 postes');
    expect(within(form).getByTestId('renewal-quote').textContent).toContain('Entreprise');
  });

  it('sans la permission de déclarer : pas de « Renouveler »', async () => {
    api(undefined, {
      subscription: {
        ...SUBSCRIPTION,
        renewal: { ...SUBSCRIPTION.renewal, kind: 'renewal', renewal_due: true },
      },
    });
    renderPage(VIEW);
    await screen.findByTestId('next-period');
    expect(screen.queryByRole('button', { name: 'Renouveler' })).toBeNull();
  });

  it('rappels d’échéance non lus en tête de page, marqués comme lus', async () => {
    api(undefined, {
      notifications: [
        {
          id: 'n-1',
          kind: 'subscription.expiry',
          step: 0,
          reference_date: '2026-10-31',
          site: { id: 'site-1', name: 'Boutique', code: 'BTQ' },
          subscription_id: 'sub-1',
          data: { days_left: 0 },
          created_at: '2026-10-31T08:00:00Z',
          read_at: null,
        },
      ],
    });
    renderPage(VIEW);
    const reminder = await screen.findByTestId('expiry-reminder');
    expect(reminder.textContent).toContain("Votre licence expire aujourd'hui : site Boutique.");
    fireEvent.click(within(reminder).getByRole('button', { name: 'Marquer comme lu' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]![0]).toMatch(/\/notifications\/n-1\/read$/);
  });
});
