// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import type { Payment, PaymentSummary, Sale } from './api';
import SalePage from './SalePage';
import { PAYMENT_METHODS_FIXTURE, paymentFixture, saleFixture } from './testData';

// Espaces insécables normalisés comme le fait Testing Library.
const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s/g, ' ');

const sale = saleFixture;
const payment = paymentFixture;

const summary = (over: Partial<PaymentSummary> = {}): PaymentSummary => ({
  total: '100000.00',
  paid_amount: '30000.00',
  remaining_amount: '70000.00',
  payment_status: 'PARTIALLY_PAID',
  ...over,
});

const ALL = [
  'sales.sale.view',
  'sales.payment.view',
  'sales.payment.create',
  'sales.payment.cancel',
];

function withToast(element: ReactNode, show: ReturnType<typeof vi.fn>) {
  const ref = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const posts = (fetchMock: ReturnType<typeof vi.fn<typeof fetch>>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');

describe('paiements sur la fiche vente', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const show = vi.fn();
  let current: Sale;
  let history: { summary: PaymentSummary | null; items: Payment[] };
  let postResponse: Response | null;

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    current = sale();
    history = {
      summary: summary(),
      items: [
        payment({ amount_received: '15000.00', change_given: '5000.00' }),
        payment({
          id: 'p2',
          number: 'PAY-000002',
          amount: '20000.00',
          method: 'MOBILE_MONEY',
          payment_method_id: 'pm2',
          method_label: 'Orange Money',
          reference: 'OM-42',
        }),
      ],
    };
    postResponse = null;
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'POST') {
        return postResponse ?? jsonResponse(payment({ id: 'p3', number: 'PAY-000003' }), 201);
      }
      if (u.includes('/payment-methods')) return jsonResponse(PAYMENT_METHODS_FIXTURE);
      if (u.includes('/payments')) {
        return jsonResponse({ sale_id: 'v1', sale_status: current.status, ...history });
      }
      return jsonResponse(current);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  const render = (permissions = ALL) =>
    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions,
      path: '/sales/:id',
      route: '/sales/v1',
    });

  it('résumé partiellement payée : total, payé, solde, état, historique', async () => {
    render();
    const summaryGroup = await screen.findByRole('group', { name: "Résumé de l'encaissement" });
    expect(within(summaryGroup).getByText(money('100000'))).toBeTruthy();
    expect(within(summaryGroup).getByText(money('30000'))).toBeTruthy();
    expect(within(summaryGroup).getByText(money('70000'))).toBeTruthy();
    expect(within(summaryGroup).getByText('Solde dû')).toBeTruthy();
    // État d'encaissement distinct du statut commercial, jamais en code technique.
    expect(screen.getAllByText('Partiellement payée').length).toBeGreaterThan(0);
    expect(screen.getByText('Validée')).toBeTruthy();
    expect(screen.queryByText('PARTIALLY_PAID')).toBeNull();
    const second = screen.getByText('PAY-000002').closest('tr') as HTMLElement;
    // Libellé figé au moment du paiement (instantané), jamais le code du type.
    expect(within(second).getByText('Orange Money')).toBeTruthy();
    expect(within(second).getByText('OM-42')).toBeTruthy();
    expect(within(second).getByText('Effectué')).toBeTruthy();
    const first = screen.getByText('PAY-000001').closest('tr') as HTMLElement;
    expect(within(first).getByText('Espèces')).toBeTruthy();
    // Espèces : montant reçu et monnaie rendue calculée par le serveur.
    expect(
      within(first).getByText(`Reçu : ${money('15000')} · monnaie rendue : ${money('5000')}`),
    ).toBeTruthy();
  });

  it.each([
    ['UNPAID', 'Non payée', '0.00', '100000.00', true],
    ['PAID', 'Payée', '100000.00', '0.00', false],
  ] as const)(
    'état %s : libellé « %s », enregistrement si solde dû',
    async (status, label, paid, rest, canPay) => {
      current = sale({ payment_status: status, paid_amount: paid, remaining_amount: rest });
      history = {
        summary: summary({ payment_status: status, paid_amount: paid, remaining_amount: rest }),
        items: status === 'PAID' ? [payment({ amount: '100000.00' })] : [],
      };
      render();
      expect((await screen.findAllByText(label)).length).toBeGreaterThan(0);
      await screen.findByRole('group', { name: "Résumé de l'encaissement" });
      expect(screen.queryAllByRole('button', { name: 'Enregistrer un paiement' }).length > 0).toBe(
        canPay,
      );
      if (status === 'UNPAID') expect(screen.getByText('Aucun paiement enregistré.')).toBeTruthy();
    },
  );

  it('enregistrer : moyens configurés du site, référence obligatoire, envoi avec clé', async () => {
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Enregistrer un paiement' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(`Solde à payer : ${money('70000')}`)).toBeTruthy();
    // Espèces par défaut : le montant saisi est le montant reçu.
    const received = (await within(dialog).findByLabelText(/^Montant reçu/)) as HTMLInputElement;
    expect(received.value).toBe('70000');
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).includes('/payment-methods?site_id=s1')),
    ).toBe(true);
    fireEvent.click(dialog.querySelector('#payment-method')?.closest('.p-dropdown') as Element);
    for (const label of ['Espèces', 'Orange Money', 'Carte bancaire']) {
      expect(
        (await screen.findAllByRole('option', { name: label, hidden: true })).length,
      ).toBeGreaterThan(0);
    }
    fireEvent.click(
      screen.getAllByRole('option', { name: 'Orange Money', hidden: true }).at(-1) as Element,
    );
    const amount = await within(dialog).findByLabelText(/^Montant\b(?! reçu)/);
    fireEvent.change(amount, { target: { value: '30 000' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer le paiement' }));
    expect(await within(dialog).findByText('Référence de la transaction obligatoire')).toBeTruthy();
    expect(posts(fetchMock)).toHaveLength(0);
    fireEvent.change(within(dialog).getByLabelText(/^Référence/), { target: { value: 'OM-1' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer le paiement' }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    const [url, init] = posts(fetchMock)[0] ?? [];
    expect(String(url)).toContain('/sales/v1/payments');
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      payment_method_id: 'pm2',
      amount: '30000',
      amount_received: null,
      reference: 'OM-1',
    });
    expect(String(body.idempotency_key)).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('espèces : montant reçu envoyé, monnaie calculée par le serveur affichée', async () => {
    postResponse = jsonResponse(
      payment({
        id: 'p3',
        number: 'PAY-000003',
        amount: '70000.00',
        amount_received: '80000.00',
        change_given: '10000.00',
      }),
      201,
    );
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Enregistrer un paiement' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(await within(dialog).findByLabelText(/^Montant reçu/), {
      target: { value: '80000' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer le paiement' }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    const body = JSON.parse(String(posts(fetchMock)[0]?.[1]?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      payment_method_id: 'pm1',
      amount: null,
      amount_received: '80000',
    });
    await waitFor(() => expect(show).toHaveBeenCalled());
    const detail = JSON.stringify(show.mock.calls[0]?.[0]).replace(/\s/g, ' ');
    expect(detail).toContain(`Monnaie à rendre : ${money('10000')}`);
  });

  it('montant invalide : erreur sans envoi ; surpaiement refusé par le serveur : message clair', async () => {
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Enregistrer un paiement' }));
    const dialog = await screen.findByRole('dialog');
    const amount = await within(dialog).findByLabelText(/^Montant/);
    for (const bad of ['0', '-5', '12,345', 'abc']) {
      fireEvent.change(amount, { target: { value: bad } });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer le paiement' }));
      expect(await within(dialog).findByText(/Montant invalide/)).toBeTruthy();
    }
    expect(posts(fetchMock)).toHaveLength(0);
    postResponse = jsonResponse(
      { code: 'payment_exceeds_balance', detail: 'x', remaining: '70000.00' },
      422,
    );
    fireEvent.change(amount, { target: { value: '80000' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer le paiement' }));
    expect(
      await within(dialog).findByText(`Le montant dépasse le solde à payer (${money('70000')}).`),
    ).toBeTruthy();
  });

  it('annulation : confirmation avec montant, motif obligatoire, historique conservé', async () => {
    history.items = [
      ...history.items,
      payment({
        id: 'p0',
        number: 'PAY-000000',
        status: 'CANCELLED',
        cancelled_by_name: 'Admin',
        cancellation_reason: 'Doublon',
      }),
    ];
    render();
    const cancelledRow = (await screen.findByText('PAY-000000')).closest('tr') as HTMLElement;
    expect(within(cancelledRow).getByText('Annulé')).toBeTruthy();
    expect(within(cancelledRow).getByText('Annulé par Admin : Doublon')).toBeTruthy();
    expect(within(cancelledRow).queryByRole('button', { name: 'Annuler le paiement' })).toBeNull();

    const row = screen.getByText('PAY-000001').closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Annuler le paiement' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(`Annuler ce paiement de ${money('10000')} ?`)).toBeTruthy();
    expect(
      within(dialog).getByText(/La vente reste validée et le stock n'est pas modifié/),
    ).toBeTruthy();
    const confirm = within(dialog).getByRole('button', { name: 'Annuler le paiement' });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/Motif d'annulation/), {
      target: { value: 'Erreur de montant' },
    });
    fireEvent.click(confirm);
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    const [url, init] = posts(fetchMock)[0] ?? [];
    expect(String(url)).toContain('/sales/v1/payments/p1/cancel');
    expect(JSON.parse(String(init?.body))).toEqual({ reason: 'Erreur de montant' });
  });

  it('consultation seule : historique visible, ni enregistrement ni annulation', async () => {
    render(['sales.sale.view', 'sales.payment.view']);
    expect(await screen.findByText('PAY-000001')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Enregistrer un paiement' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Annuler le paiement' })).toBeNull();
  });

  it('sans permission de consultation des paiements : aucune section ni requête', async () => {
    render(['sales.sale.view']);
    expect(await screen.findByRole('heading', { name: /VENT-BOU-2026-000123/ })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Paiements' })).toBeNull();
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/payments'))).toBe(false);
  });
});
