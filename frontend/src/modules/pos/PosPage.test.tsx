// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, pageOf, renderWithCapabilities, SITES } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

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
  sale: {
    id: 'v9',
    number: 'VTE-000042',
    site_id: 's1',
    site_name: 'Boutique',
    customer_id: null,
    customer_code: null,
    customer_name: null,
    status: 'VALIDATED',
    channel: 'POS',
    sale_date: '2026-09-25',
    notes: null,
    subtotal: '11000.00',
    total: '11000.00',
    line_count: 1,
    created_at: '2026-09-25T10:00:00Z',
    updated_at: '2026-09-25T10:00:00Z',
    created_by_name: 'Moi',
    validated_at: '2026-09-25T10:00:00Z',
    validated_by_name: 'Moi',
    cancelled_at: null,
    cancelled_by_name: null,
    cancellation_reason: null,
    paid_amount: '11000.00',
    remaining_amount: '0.00',
    payment_status: 'PAID',
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
  },
  payments: [
    {
      id: 'p1',
      number: 'PAY-000001',
      sale_id: 'v9',
      sale_number: 'VTE-000042',
      site_id: 's1',
      amount: '6000.00',
      method: 'CASH',
      provider: null,
      status: 'COMPLETED',
      reference: null,
      paid_at: '2026-09-25T10:00:00Z',
      created_at: '2026-09-25T10:00:00Z',
      created_by_name: 'Moi',
      cancelled_at: null,
      cancelled_by_name: null,
      cancellation_reason: null,
    },
    {
      id: 'p2',
      number: 'PAY-000002',
      sale_id: 'v9',
      sale_number: 'VTE-000042',
      site_id: 's1',
      amount: '5000.00',
      method: 'MOBILE_MONEY',
      provider: null,
      status: 'COMPLETED',
      reference: null,
      paid_at: '2026-09-25T10:00:00Z',
      created_at: '2026-09-25T10:00:00Z',
      created_by_name: 'Moi',
      cancelled_at: null,
      cancelled_by_name: null,
      cancellation_reason: null,
    },
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
    const methods = within(dialog).getByRole('group', { name: 'Ajouter un moyen de paiement' });
    fireEvent.click(within(methods).getByRole('button', { name: 'Espèces' }));
    fireEvent.change(within(dialog).getByLabelText('Montant du paiement 1'), {
      target: { value: '6000' },
    });
    fireEvent.click(within(methods).getByRole('button', { name: 'Mobile Money' }));
    // Deuxième moyen : le reste (5 000) est proposé.
    expect((within(dialog).getByLabelText('Montant du paiement 2') as HTMLInputElement).value).toBe(
      '5000',
    );
    expect(text(within(dialog).getByTestId('pos-remaining'))).toBe(money('0'));
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
        { amount: '6000', method: 'CASH', cash_register_id: null },
        { amount: '5000', method: 'MOBILE_MONEY', cash_register_id: null },
      ],
    });
    expect(typeof body.idempotency_key).toBe('string');
    // Aucun prix ni total envoyé : le serveur fait foi.
    expect(JSON.stringify(body)).not.toMatch(/price|total/);
    const receipt = await screen.findByTestId('pos-receipt');
    expect(screen.getByRole('dialog', { name: 'Vente VTE-000042 enregistrée' })).toBeTruthy();
    expect(text(receipt)).toContain('Espèces');
    expect(text(receipt)).toContain('Mobile Money');
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
    expect(text(confirm)).toContain('sans débiteur identifié');
    await act(async () => {
      within(confirm).getByRole('button', { name: 'Valider la vente (F10)' }).click();
    });
    expect(await within(confirm).findByText(/Aucune caisse ouverte sur le site/)).toBeTruthy();
    expect(screen.getByLabelText('Quantité de Ciment 50 kg')).toBeTruthy();
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
