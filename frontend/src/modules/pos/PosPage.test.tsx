// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, pageOf, renderWithCapabilities, SITES } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { PAYMENT_METHODS_FIXTURE, paymentFixture, saleFixture } from '@/modules/sales/testData';

import type { PosArticle } from './api';
import PosPage from './PosPage';

const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s/g, ' ');
const text = (el: Element) => (el.textContent ?? '').replace(/\s/g, ' ');

const ARTICLES: PosArticle[] = [
  {
    article_id: 'a1',
    reference: 'CIM-50',
    designation: 'Ciment 50 kg',
    unit: 'sac',
    category_name: 'Matériaux',
    sale_price: '5500.00',
    quantity: '40.000',
    is_active: true,
  },
  {
    article_id: 'a2',
    reference: 'FER-8',
    designation: 'Fer à béton 8 mm',
    unit: 'barre',
    category_name: 'Matériaux',
    sale_price: '2500.00',
    quantity: '0.000',
    is_active: true,
  },
  {
    article_id: 'a3',
    reference: 'OLD-1',
    designation: 'Ancien article',
    unit: 'u',
    category_name: null,
    sale_price: '100.00',
    quantity: '5.000',
    is_active: false,
  },
];

const SELLER = [
  'pos.terminal.use',
  'sales.sale.view',
  'sales.sale.create',
  'sales.sale.validate',
  'sales.payment.create',
];

const RESULT = {
  replayed: false,
  sale: saleFixture({
    id: 'v9',
    number: 'VENT-BOU-2026-000042',
    customer_id: null,
    customer_code: null,
    customer_name: null,
    sale_date: '2026-09-25',
    subtotal: '11000.00',
    total: '11000.00',
    paid_amount: '11000.00',
    remaining_amount: '0.00',
    payment_status: 'PAID',
    is_credit: false,
    credit_status: null,
    lines: [
      {
        id: 'l1',
        line_no: 1,
        article_id: 'a1',
        article_reference: 'CIM-50',
        article_designation: 'Ciment 50 kg',
        unit: 'sac',
        quantity: '2.000',
        unit_price: '5500.00',
        line_total: '11000.00',
      },
    ],
  }),
  payments: [
    paymentFixture({
      id: 'p2',
      number: 'PAY-000002',
      sale_id: 'v9',
      sale_number: 'VENT-BOU-2026-000042',
      amount: '5000.00',
      method: 'MOBILE_MONEY',
      payment_method_id: 'pm2',
      method_label: 'Orange Money',
      reference: 'OM-9',
    }),
    // Espèces : 10 000 reçus pour 6 000 dus, monnaie calculée par le serveur.
    paymentFixture({
      sale_id: 'v9',
      sale_number: 'VENT-BOU-2026-000042',
      amount: '6000.00',
      amount_received: '10000.00',
      change_given: '4000.00',
    }),
  ],
};

function withToast(element: ReactNode) {
  const ref = { current: { show: vi.fn() } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const fetchMock = vi.fn<typeof fetch>();
const calls = (part: string) =>
  fetchMock.mock.calls.map(([url]) => String(url)).filter((u) => u.includes(part));
const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
let checkoutResponse: () => Response;

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  checkoutResponse = () => jsonResponse(RESULT, 201);
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (init?.method === 'POST') return checkoutResponse();
    if (u.includes('/pos/articles')) return jsonResponse(ARTICLES);
    if (u.includes('/payment-methods')) return jsonResponse(PAYMENT_METHODS_FIXTURE);
    return pageOf([]);
  });
});
afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const render = (permissions = SELLER, sites = [SITES[0] as (typeof SITES)[number]]) =>
  renderWithCapabilities(withToast(<PosPage />), {
    permissions,
    sites,
    path: '/pos',
    route: '/pos',
  });

const tile = (name: string) => screen.getByRole('button', { name: `Ajouter ${name} au panier` });

describe('point de vente', () => {
  it('recherche serveur ; prix, stock et statut ; article inactif non ajoutable', async () => {
    render();
    const ciment = await screen.findByRole('button', { name: 'Ajouter Ciment 50 kg au panier' });
    expect(text(ciment)).toContain(money('5500'));
    expect(text(ciment)).toContain('Stock : 40 sac');
    expect(text(tile('Fer à béton 8 mm'))).toContain('Rupture');
    expect((tile('Ancien article') as HTMLButtonElement).disabled).toBe(true);
    expect(calls('/pos/articles?').at(-1)).toContain('site_id=s1');
    fireEvent.change(screen.getByLabelText('Rechercher un article (F2)'), {
      target: { value: 'fer' },
    });
    await waitFor(() => expect(calls('/pos/articles?').at(-1)).toContain('search=fer'));
    expect(document.body.textContent).not.toMatch(/MOBILE_MONEY|VALIDATED|CASH\b/);
  });

  it('panier : ajout, un article une fois, quantités, suppression, total', async () => {
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter Ciment 50 kg au panier' }));
    fireEvent.click(tile('Ciment 50 kg'));
    const cart = screen.getByRole('list', { name: 'Panier' });
    expect(within(cart).getAllByRole('listitem')).toHaveLength(1);
    expect((screen.getByLabelText('Quantité de Ciment 50 kg') as HTMLInputElement).value).toBe('2');
    expect(text(screen.getByTestId('pos-total'))).toBe(money('11000'));
    fireEvent.click(screen.getByRole('button', { name: 'Augmenter la quantité de Ciment 50 kg' }));
    expect(text(screen.getByTestId('pos-total'))).toBe(money('16500'));
    fireEvent.change(screen.getByLabelText('Quantité de Ciment 50 kg'), {
      target: { value: '0' },
    });
    expect(
      (screen.getByRole('button', { name: 'Valider la vente (F10)' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Retirer Ciment 50 kg du panier' }));
    expect(screen.getByText('Panier vide : ajoutez des articles.')).toBeTruthy();
  });

  it('raccourcis : F2 recherche, Entrée ajoute le premier article, F8 paiement, F10 validation', async () => {
    render();
    await screen.findByRole('button', { name: 'Ajouter Ciment 50 kg au panier' });
    const search = screen.getByLabelText('Rechercher un article (F2)');
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.keyDown(window, { key: 'F2' });
    expect(document.activeElement).toBe(search);
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(screen.getByLabelText('Quantité de Ciment 50 kg')).toBeTruthy();
    fireEvent.keyDown(window, { key: 'F8' });
    expect(await screen.findByRole('dialog', { name: 'Paiements (F8)' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Annuler' }));
    fireEvent.keyDown(window, { key: 'F10' });
    expect(await screen.findByRole('dialog', { name: 'Valider la vente ?' })).toBeTruthy();
  });

  it('paiement multiple puis validation : une requête, clé d’idempotence, reçu du serveur', async () => {
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter Ciment 50 kg au panier' }));
    fireEvent.click(tile('Ciment 50 kg'));
    fireEvent.click(screen.getByRole('button', { name: 'Paiement (F8)' }));
    const dialog = await screen.findByRole('dialog', { name: 'Paiements (F8)' });
    // Moyens configurés et disponibles sur le site (libellés de l'entreprise).
    const methods = await within(dialog).findByRole('group', {
      name: 'Ajouter un moyen de paiement',
    });
    expect(calls('/payment-methods?site_id=s1').length).toBeGreaterThan(0);
    fireEvent.click(within(methods).getByRole('button', { name: 'Orange Money' }));
    fireEvent.change(within(dialog).getByLabelText('Montant du paiement 1'), {
      target: { value: '5000' },
    });
    fireEvent.click(within(methods).getByRole('button', { name: 'Espèces' }));
    // Espèces : le reste (6 000) est proposé comme montant reçu ; le client donne 10 000.
    const received = within(dialog).getByLabelText(
      'Montant reçu du paiement 2',
    ) as HTMLInputElement;
    expect(received.value).toBe('6000');
    fireEvent.change(received, { target: { value: '10000' } });
    expect(text(within(dialog).getByTestId('pos-remaining'))).toBe(money('0'));
    // Référence obligatoire pour Orange Money : refus avant envoi.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Appliquer' }));
    expect(within(dialog).getByText('Référence de la transaction obligatoire')).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Référence du paiement 1'), {
      target: { value: 'OM-9' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Appliquer' }));
    fireEvent.click(screen.getByRole('button', { name: 'Valider la vente (F10)' }));
    const confirm = await screen.findByRole('dialog', { name: 'Valider la vente ?' });
    expect(text(within(confirm).getByTestId('confirm-remaining'))).toBe(money('0'));
    await act(async () => {
      within(confirm).getByRole('button', { name: 'Valider la vente (F10)' }).click();
    });
    await waitFor(() => expect(posts()).toHaveLength(1));
    const [url, init] = posts()[0] ?? [];
    expect(String(url)).toContain('/api/v1/pos/checkout');
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      site_id: 's1',
      customer_id: null,
      lines: [{ article_id: 'a1', quantity: '2' }],
      payments: [
        {
          payment_method_id: 'pm2',
          amount: '5000',
          amount_received: null,
          reference: 'OM-9',
          cash_register_id: null,
        },
        // Espèces : montant reçu seulement ; monnaie calculée par le serveur.
        {
          payment_method_id: 'pm1',
          amount: null,
          amount_received: '10000',
          reference: null,
          cash_register_id: null,
        },
      ],
      credit_override: null,
    });
    expect(typeof body.idempotency_key).toBe('string');
    // Aucun prix ni total envoyé : le serveur fait foi.
    expect(JSON.stringify(body)).not.toMatch(/price|total/);
    const receipt = await screen.findByTestId('pos-receipt');
    expect(
      screen.getByRole('dialog', { name: 'Vente VENT-BOU-2026-000042 enregistrée' }),
    ).toBeTruthy();
    expect(text(receipt)).toContain('Espèces');
    expect(text(receipt)).toContain('Orange Money');
    expect(text(receipt)).toContain(`Espèces reçues ${money('10000')} — monnaie rendue`);
    expect(text(within(receipt).getByTestId('receipt-change'))).toBe(money('4000'));
    expect(text(within(receipt).getByTestId('receipt-remaining'))).toBe(money('0'));
    // Nouvelle vente : panier vidé, nouvelle clé.
    fireEvent.click(screen.getByRole('button', { name: 'Nouvelle vente' }));
    expect(screen.getByText('Panier vide : ajoutez des articles.')).toBeTruthy();
  });

  it('erreur métier du serveur (limite de crédit, caisse) : message traduit, panier conservé', async () => {
    checkoutResponse = () => jsonResponse({ code: 'cash_session_required', status: 422 }, 422);
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter Ciment 50 kg au panier' }));
    fireEvent.click(screen.getByRole('button', { name: 'Valider la vente (F10)' }));
    const confirm = await screen.findByRole('dialog', { name: 'Valider la vente ?' });
    // Reste dû sans client : avertissement.
    expect(text(confirm)).toContain('une vente à crédit exige un client identifié');
    await act(async () => {
      within(confirm).getByRole('button', { name: 'Valider la vente (F10)' }).click();
    });
    expect(
      await within(confirm).findByText(/Aucune session de caisse ouverte à votre nom/),
    ).toBeTruthy();
    expect(screen.getByLabelText('Quantité de Ciment 50 kg')).toBeTruthy();
  });

  it('limite de crédit : dépassement proposé si le serveur le permet, justification envoyée', async () => {
    checkoutResponse = () =>
      jsonResponse(
        {
          code: 'credit_limit_exceeded',
          detail: 'x',
          credit_limit: '1000.00',
          sale_exposure: '5500.00',
          override_allowed: true,
        },
        422,
      );
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter Ciment 50 kg au panier' }));
    fireEvent.click(screen.getByRole('button', { name: 'Valider la vente (F10)' }));
    const confirm = await screen.findByRole('dialog', { name: 'Valider la vente ?' });
    await act(async () => {
      within(confirm).getByRole('button', { name: 'Valider la vente (F10)' }).click();
    });
    expect(await within(confirm).findByText(/Limite de crédit du client dépassée/)).toBeTruthy();
    const override = within(confirm).getByRole('button', {
      name: 'Autoriser le dépassement et valider',
    });
    expect((override as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(confirm).getByLabelText(/Justification du dépassement/), {
      target: { value: 'Client fidèle' },
    });
    checkoutResponse = () => jsonResponse(RESULT, 201);
    await act(async () => {
      override.click();
    });
    await waitFor(() => expect(posts()).toHaveLength(2));
    const body = JSON.parse(String(posts()[1]?.[1]?.body)) as Record<string, unknown>;
    expect(body.credit_override).toEqual({ reason: 'Client fidèle' });
    // Même panier : même clé d'idempotence.
    expect(body.idempotency_key).toBe(
      (JSON.parse(String(posts()[0]?.[1]?.body)) as Record<string, unknown>).idempotency_key,
    );
  });

  it('permissions : sans droit d’encaisser ni de vendre', async () => {
    render(['pos.terminal.use', 'sales.sale.create', 'sales.sale.validate']);
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter Ciment 50 kg au panier' }));
    expect(screen.queryByRole('button', { name: 'Paiement (F8)' })).toBeNull();
    cleanup();
    render(['pos.terminal.use']);
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter Ciment 50 kg au panier' }));
    expect(screen.getByText(/pas le droit d'enregistrer/)).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Valider la vente (F10)' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it('plusieurs sites sans site sélectionné : choix du site avant toute recherche', async () => {
    render(SELLER, SITES);
    expect(await screen.findByText('Choisissez le site de vente pour commencer.')).toBeTruthy();
    expect(calls('/pos/articles')).toHaveLength(0);
  });
});
