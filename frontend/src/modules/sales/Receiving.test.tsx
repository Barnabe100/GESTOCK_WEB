// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import i18next from 'i18next';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  PosPaymentDialog,
  toCheckoutPayment,
  type PosPayment,
} from '@/modules/pos/PosPaymentDialog';
import { jsonResponse, renderWithCapabilities, SITES } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { accountText, type PaymentAccount, type PaymentSummary } from './api';
import { toPaymentInput } from './PaymentFields';
import PaymentMethodsPage from './PaymentMethodsPage';
import { SaleReceipt } from './receipt/SaleReceipt';
import type { Receipt } from './receipt/api';
import SalePage from './SalePage';
import { paymentFixture, paymentMethodFixture, saleFixture } from './testData';

/** R6.5 (ADR-0051) : moyens de réception — caisse, confirmation, administration, reçu. */

const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
const t = i18next.t.bind(i18next) as unknown as Parameters<typeof toPaymentInput>[0];

const account = (over: Partial<PaymentAccount> = {}): PaymentAccount => ({
  id: 'a1',
  payment_method_id: 'pm2',
  site_id: null,
  site_name: null,
  shared: true,
  identifier_type: 'MERCHANT_CODE',
  custom_label: null,
  value: '123456',
  holder_name: 'Boutique Centre',
  instructions: 'Indiquez le numéro de la vente.',
  sort_order: 0,
  is_active: true,
  ...over,
});

const mobile = (accounts: PaymentAccount[]) =>
  paymentMethodFixture({
    id: 'pm2',
    label: 'Mobile Money',
    kind: 'MOBILE_MONEY',
    provider_name: 'Opérateur Local',
    provider_label: 'Opérateur Local',
    accounts,
    sort_order: 20,
  });

const fetchMock = vi.fn<typeof fetch>();
const show = vi.fn();
const posts = () =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'POST' || init?.method === 'PATCH')
    .map(([u, init]) => [String(u), JSON.parse(String(init?.body ?? 'null'))] as const);

function withToast(element: ReactNode) {
  const ref = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  show.mockReset();
  vi.unstubAllGlobals();
});

describe('saisie d’un paiement : confirmation explicite (R6.5)', () => {
  it('hors espèces : choisir le moyen ne suffit pas ; identifiant et confirmation envoyés', () => {
    const methods = [paymentMethodFixture(), mobile([account()])];
    const draft = { methodId: 'pm2', amount: '5000', reference: '', accountId: 'a1' };
    const refused = toPaymentInput(t, draft, methods);
    expect('errors' in refused && refused.errors.confirmed).toBe(
      'Confirmez la réception effective des fonds.',
    );
    const ok = toPaymentInput(t, { ...draft, confirmed: true }, methods);
    expect('input' in ok && ok.input).toMatchObject({
      payment_method_id: 'pm2',
      amount: '5000',
      payment_method_account_id: 'a1',
      received_confirmed: true,
    });
    // Espèces : aucune confirmation demandée, jamais d'identifiant.
    const cash = toPaymentInput(t, { methodId: 'pm1', amount: '5000', reference: '' }, methods);
    expect('input' in cash && cash.input).toMatchObject({
      amount_received: '5000',
      payment_method_account_id: null,
      received_confirmed: false,
    });
  });
});

describe('enregistrer un paiement sur la fiche vente (R6.5)', () => {
  const summary: PaymentSummary = {
    total: '100000.00',
    paid_amount: '0.00',
    remaining_amount: '100000.00',
    payment_status: 'UNPAID',
  };

  const serve = (accounts: PaymentAccount[]) =>
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'POST') return jsonResponse(paymentFixture({ id: 'p9' }), 201);
      if (u.includes('/payment-methods')) {
        return jsonResponse([paymentMethodFixture(), mobile(accounts)]);
      }
      if (u.includes('/payments')) {
        return jsonResponse({ sale_id: 'v1', sale_status: 'VALIDATED', summary, items: [] });
      }
      return jsonResponse(saleFixture({ payment_status: 'UNPAID', remaining_amount: '100000.00' }));
    });

  const open = async () => {
    renderWithCapabilities(withToast(<SalePage />), {
      permissions: ['sales.sale.view', 'sales.payment.view', 'sales.payment.create'],
      path: '/sales/:id',
      route: '/sales/v1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Enregistrer un paiement' }));
    const dialog = await screen.findByRole('dialog');
    // Moyens du site chargés (serveur) avant le choix.
    await waitFor(() => expect(dialog.querySelector('#payment-method')).not.toBeNull());
    fireEvent.click(dialog.querySelector('#payment-method')?.closest('.p-dropdown') as Element);
    fireEvent.click(
      screen.getAllByRole('option', { name: 'Mobile Money', hidden: true }).at(-1) as Element,
    );
    return dialog;
  };

  it('un seul identifiant : présélectionné et affiché ; envoi refusé sans confirmation', async () => {
    serve([account()]);
    const dialog = await open();
    const details = await within(dialog).findByTestId('payment-account-details');
    expect(text(details)).toContain('Code marchand 123456 (Boutique Centre)');
    expect(text(details)).toContain('Indiquez le numéro de la vente.');
    expect(text(within(dialog).getByTestId('payment-provider'))).toContain('Opérateur Local');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer le paiement' }));
    expect(
      await within(dialog).findByText('Confirmez la réception effective des fonds.'),
    ).toBeTruthy();
    expect(posts()).toHaveLength(0);
    fireEvent.click(within(dialog).getByLabelText("J'ai vérifié que les fonds ont bien été reçus"));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer le paiement' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]?.[1]).toMatchObject({
      payment_method_account_id: 'a1',
      received_confirmed: true,
      reference: null,
    });
  });

  it('plusieurs identifiants : aucun présélectionné, choix du caissier ou aucun', async () => {
    serve([
      account(),
      account({ id: 'a2', shared: false, site_id: 's1', site_name: 'Boutique', value: '777' }),
    ]);
    const dialog = await open();
    await within(dialog).findByLabelText('Identifiant de réception', {
      selector: 'input, select, span, div',
    });
    expect(within(dialog).queryByTestId('payment-account-details')).toBeNull();
    fireEvent.click(dialog.querySelector('#payment-account')?.closest('.p-dropdown') as Element);
    fireEvent.click(
      screen
        .getAllByRole('option', { name: /777 \(Boutique Centre\) · Boutique/, hidden: true })
        .at(-1) as Element,
    );
    expect(text(await within(dialog).findByTestId('payment-account-details'))).toContain('777');
    fireEvent.click(within(dialog).getByLabelText("J'ai vérifié que les fonds ont bien été reçus"));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer le paiement' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]?.[1]).toMatchObject({ payment_method_account_id: 'a2' });
  });

  it('aucun identifiant configuré : fonctionnement inchangé, confirmation toujours exigée', async () => {
    serve([]);
    const dialog = await open();
    expect(
      within(dialog).queryByLabelText('Identifiant de réception', {
        selector: 'input, select, span, div',
      }),
    ).toBeNull();
    fireEvent.click(within(dialog).getByLabelText("J'ai vérifié que les fonds ont bien été reçus"));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer le paiement' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]?.[1]).toMatchObject({
      payment_method_account_id: null,
      received_confirmed: true,
    });
  });
});

describe('point de vente : identifiant et confirmation par paiement (R6.5)', () => {
  it('présélection, confirmation obligatoire, corps de l’API', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse([paymentMethodFixture(), mobile([account()])]),
    );
    const onSave = vi.fn();
    renderWithCapabilities(
      <PosPaymentDialog
        total="10000.00"
        siteId="s1"
        initial={[]}
        cashRegisterId={null}
        onSave={onSave}
        onClose={() => undefined}
      />,
      { permissions: ['pos.terminal.use'], sites: [SITES[0]!] },
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Mobile Money' }));
    expect(text(await screen.findByTestId('pos-pay-1-account-details'))).toContain(
      'Code marchand 123456',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Appliquer' }));
    expect(await screen.findByText('Confirmez la réception effective des fonds.')).toBeTruthy();
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("J'ai vérifié que les fonds ont bien été reçus"));
    fireEvent.click(screen.getByRole('button', { name: 'Appliquer' }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const [saved] = onSave.mock.calls[0] as [PosPayment[]];
    expect(toCheckoutPayment(saved[0]!, null)).toMatchObject({
      payment_method_id: 'pm2',
      payment_method_account_id: 'a1',
      received_confirmed: true,
      amount: '10000',
    });
  });
});

describe('administration des moyens de réception (R6.5)', () => {
  const serve = (accounts: PaymentAccount[] = []) =>
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method && init.method !== 'GET') {
        return jsonResponse(
          init.method === 'POST' && u.includes('/accounts') ? account() : mobile([]),
          201,
        );
      }
      if (u.includes('/public/geo/countries')) {
        return jsonResponse([
          {
            code: 'BF',
            name: 'Burkina Faso',
            currency: 'XOF',
            calling_code: 226,
            timezone: 'Africa/Abidjan',
          },
          {
            code: 'FR',
            name: 'France',
            currency: 'EUR',
            calling_code: 33,
            timezone: 'Europe/Paris',
          },
        ]);
      }
      if (u.includes('/payment-providers')) return jsonResponse([]);
      if (u.includes('/accounts')) return jsonResponse(accounts);
      return jsonResponse([
        paymentMethodFixture(),
        mobile([]),
        paymentMethodFixture({
          id: 'pm4',
          label: 'Carte EUR',
          kind: 'CARD',
          country_code: null,
          currency: 'EUR',
        }),
      ]);
    });

  const page = (route = '/sales/payment-methods') =>
    renderWithCapabilities(withToast(<PaymentMethodsPage />), {
      permissions: ['sales.payment_method.manage'],
      path: '/sales/payment-methods',
      route,
    });

  it('liste : opérateur, pays et devise ; création avec opérateur personnalisé', async () => {
    serve();
    page();
    const row = (await screen.findByText('Carte EUR')).closest('tr') as HTMLElement;
    expect(text(row)).toContain('International · EUR');
    expect(screen.getByRole('row', { name: /Opérateur Local/ })).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Nouveau moyen' })[0]!);
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/^Libellé/), { target: { value: 'Mobile 2' } });
    fireEvent.click(dialog.querySelector('#pm-provider')?.closest('.p-dropdown') as Element);
    fireEvent.click(
      screen.getAllByRole('option', { name: 'Opérateur personnalisé…', hidden: true }).at(-1)!,
    );
    fireEvent.change(await within(dialog).findByLabelText(/^Nom de l'opérateur/), {
      target: { value: 'Mon opérateur' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toEqual([
      '/api/v1/payment-methods',
      expect.objectContaining({
        label: 'Mobile 2',
        provider_code: null,
        provider_name: 'Mon opérateur',
        currency: 'XOF',
      }),
    ]);
  });

  it('devise différente de celle des ventes : avertissement explicite', async () => {
    serve();
    page();
    const row = (await screen.findByText('Carte EUR')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Modifier' }));
    const dialog = await screen.findByRole('dialog');
    expect(text(dialog)).toContain('ce moyen ne pourra pas encaisser les ventes, tenues en XOF');
  });

  it('identifiants : communs et propres à un site distingués, création commune', async () => {
    serve([
      account(),
      account({ id: 'a2', shared: false, site_id: 's1', site_name: 'Boutique', value: '777' }),
    ]);
    page();
    const row = await screen.findByRole('row', { name: /Opérateur Local/ });
    fireEvent.click(within(row).getByRole('button', { name: 'Identifiants de réception' }));
    const dialog = await screen.findByRole('dialog', { name: /Identifiants de réception/ });
    const shared = (await within(dialog).findByText('123456')).closest('tr') as HTMLElement;
    expect(text(shared)).toContain('Tous les sites');
    expect(text(within(dialog).getByText('777').closest('tr'))).toContain('Boutique');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Nouvel identifiant' }));
    const form = await screen.findByRole('dialog', { name: 'Nouvel identifiant' });
    fireEvent.change(within(form).getByLabelText(/^Numéro ou code/), { target: { value: '999' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toEqual([
      '/api/v1/payment-methods/pm2/accounts',
      expect.objectContaining({ site_id: null, identifier_type: 'PHONE', value: '999' }),
    ]);
  });

  it('venue de la page Sites : site mis en avant et préselectionné', async () => {
    serve([account()]);
    page('/sales/payment-methods?site=s1');
    expect(await screen.findByText(/Site : Boutique/)).toBeTruthy();
  });
});

describe('reçu : instantané des coordonnées utilisées (R6.5)', () => {
  it('libellé, opérateur, type, valeur et titulaire', () => {
    const receipt = {
      sale_id: 'v1',
      number: 'VENT-BTQ-2026-000001',
      site_name: 'Boutique',
      issued_at: '2026-11-01T10:00:00Z',
      cashier_name: 'Moi',
      customer_name: null,
      issuer: { name: 'Société', trade_name: null, logo_url: null, contact: [], identifiers: [] },
      lines: [],
      total: '5000.00',
      paid_amount: '5000.00',
      remaining_amount: '0.00',
      payment_status: 'PAID',
      is_credit: false,
      amount_received: '5000.00',
      change_given: null,
      print_count: 0,
      payments: [
        {
          method: 'MOBILE_MONEY',
          method_label: 'Mobile Money',
          amount: '5000.00',
          amount_received: null,
          change_given: null,
          paid_at: '2026-11-01T10:00:00Z',
          currency: 'XOF',
          provider_label: 'Opérateur Local',
          account_identifier_type: 'MERCHANT_CODE',
          account_label: null,
          account_value: '123456',
          account_holder: 'Boutique Centre',
        },
      ],
    } as unknown as Receipt;
    renderWithCapabilities(<SaleReceipt receipt={receipt} />, { permissions: [] });
    const line = screen.getByTestId('receipt-payment');
    expect(text(within(line).getByTestId('receipt-provider'))).toBe('Opérateur Local');
    expect(text(within(line).getByTestId('receipt-account'))).toBe(
      'Code marchand 123456 (Boutique Centre)',
    );
  });
});

describe('téléphones locaux et internationaux (R6.5)', () => {
  it('local : indicatif du pays affiché à côté, jamais fusionné ; international tel quel', () => {
    const tt = i18next.t.bind(i18next) as unknown as (key: string) => string;
    const base = { identifier_type: 'PHONE' as const, custom_label: null, holder_name: null };
    expect(
      accountText(tt, { ...base, value: '70112233', country_code: 'BF', calling_code: 226 }),
    ).toBe('Téléphone 70112233 · BF +226');
    expect(
      accountText(tt, { ...base, value: '+22670112233', country_code: 'BF', calling_code: 226 }),
    ).toBe('Téléphone +22670112233');
    // Sans pays de rattachement : rien n'est supposé.
    expect(accountText(tt, { ...base, value: '70112233' })).toBe('Téléphone 70112233');
  });
});
