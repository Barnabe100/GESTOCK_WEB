// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import i18next from 'i18next';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SALE_CHANNELS } from '@/modules/sales/api';
import { PAYMENT_METHODS_FIXTURE } from '@/modules/sales/testData';
import { formatMoney } from '@/shared/lib/decimal';
import { SITES, jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { ORDERS_REFRESH_MS } from './api';
import OrderCreatePage from './OrderCreatePage';
import OrderPage from './OrderPage';
import OrderSettingsPage from './OrderSettingsPage';
import OrdersPage from './OrdersPage';

/** Palier R2-E (ADR-0049) : interface des commandes — suivi, saisie, détail, règlement. */

const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s+/g, ' ');
const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

const VIEW = 'restaurant.orders.order.view';
const CREATE = 'restaurant.orders.order.create';
const MENU_VIEW = 'restaurant.menu.view';
const STAFF = [
  VIEW,
  CREATE,
  'restaurant.orders.order.claim',
  'restaurant.orders.order.prepare',
  'restaurant.orders.order.serve',
  'restaurant.orders.order.cancel',
];
const CASHIER = [
  ...STAFF,
  'sales.sale.create',
  'sales.sale.validate',
  'sales.sale.view',
  'sales.payment.create',
];

const line = (over: Record<string, unknown> = {}) => ({
  id: 'l1',
  line_no: 1,
  menu_item_id: 'i1',
  article_id: 'a1',
  packaging_id: null,
  label: 'Poulet braisé',
  packaging_name: null,
  unit: 'portion',
  conversion: null,
  unit_price: '2500.00',
  quantity: '2.000',
  base_quantity: '2.000',
  line_total: '5000.00',
  note: 'Bien cuit',
  status: 'RECEIVED',
  created_at: '2026-10-10T12:00:00Z',
  prepared_at: null,
  ready_at: null,
  served_at: null,
  cancelled_at: null,
  cancel_reason: null,
  ...over,
});

const order = (over: Record<string, unknown> = {}) => ({
  id: 'o1',
  site_id: 's1',
  site_name: 'Boutique',
  business_date: '2026-10-10',
  daily_number: 12,
  channel: 'STAFF',
  service_mode: 'ON_SITE',
  customer_id: null,
  customer_name: null,
  call_name: 'Table 4',
  status: 'OPEN',
  prep_status: 'RECEIVED',
  settlement_status: 'UNSETTLED',
  payment_timing: 'AT_END',
  assigned_user_id: null,
  assigned_name: null,
  assigned_at: null,
  created_by: 'u-me',
  created_by_name: 'Moi',
  total: '5500.00',
  sale_id: null,
  sale_number: null,
  payment_status: null,
  amount_due: null,
  line_counts: { received: 2, in_preparation: 0, ready: 0, served: 0, cancelled: 0 },
  created_at: '2026-10-10T12:00:00Z',
  last_served_at: null,
  updated_at: '2026-10-10T12:00:00Z',
  closed_at: null,
  cancelled_at: null,
  cancel_reason: null,
  version: 1,
  lines: [
    line(),
    line({
      id: 'l2',
      line_no: 2,
      menu_item_id: 'i2',
      label: 'Coca-Cola 33 cl',
      unit: 'bouteille',
      unit_price: '500.00',
      quantity: '1.000',
      base_quantity: '1.000',
      line_total: '500.00',
      note: null,
    }),
  ],
  ...over,
});

const settledOrder = (over: Record<string, unknown> = {}) =>
  order({
    settlement_status: 'SETTLED',
    sale_id: 'v9',
    sale_number: 'VENT-BTQ-2026-000009',
    payment_status: 'PAID',
    amount_due: '0.00',
    ...over,
  });

const ticket = {
  company_name: 'Maquis du Lac',
  site_name: 'Boutique',
  business_date: '2026-10-10',
  daily_number: 12,
  call_name: 'Table 4',
  service_mode: 'ON_SITE',
  created_at: '2026-10-10T12:00:00Z',
  timezone: 'UTC',
  lines: [
    {
      label: 'Poulet braisé',
      packaging_name: null,
      quantity: '2.000',
      unit: 'portion',
      note: 'Bien cuit',
    },
    {
      label: 'Coca-Cola 33 cl',
      packaging_name: null,
      quantity: '1.000',
      unit: 'bouteille',
      note: null,
    },
  ],
};

/** Reçu de la vente du règlement (construit par le serveur depuis la vente persistée). */
const receipt = {
  sale_id: 'v9',
  number: 'VENT-BTQ-2026-000009',
  site_name: 'Boutique',
  issued_at: '2026-10-10T12:30:00Z',
  cashier_name: 'Moi',
  customer_name: null,
  issuer: { name: 'Maquis du Lac', trade_name: null, logo_url: null, contact: [], identifiers: [] },
  lines: [],
  total: '5500.00',
  paid_amount: '5500.00',
  remaining_amount: '0.00',
  payment_status: 'PAID',
  is_credit: false,
  payments: null,
  amount_received: '10000.00',
  change_given: '4500.00',
  print_count: 0,
};

const events = [
  {
    id: 'e1',
    event_type: 'CREATED',
    actor_kind: 'STAFF',
    actor_user_id: 'u-me',
    actor_name: 'Moi',
    reason: null,
    line_ids: ['l1', 'l2'],
    data: {},
    occurred_at: '2026-10-10T12:00:00Z',
  },
];

const menuItem = (over: Record<string, unknown> = {}) => ({
  id: 'i1',
  site_id: 's1',
  site_name: 'Boutique',
  section_id: 'sec1',
  section_name: 'Grillades',
  section_active: true,
  article_id: 'a1',
  reference: 'POULET',
  designation: 'Poulet braisé',
  unit: 'portion',
  packaging_id: null,
  packaging_name: null,
  conversion: null,
  display_name: null,
  description: null,
  sort_order: 0,
  is_active: true,
  available: true,
  unavailable_reason: null,
  price: '2500.00',
  orderable: true,
  blockers: [],
  created_at: '2026-10-09T08:00:00Z',
  updated_at: '2026-10-09T08:00:00Z',
  ...over,
});

const fetchMock = vi.fn<typeof fetch>();
const printMock = vi.fn();
const show = vi.fn();
const calls = (method = 'GET') =>
  fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? 'GET') === method)
    .map(([u, init]) => [String(u), init?.body ? JSON.parse(String(init.body)) : null] as const);

const withToast = (node: ReactNode) => {
  const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={toast}>{node}</ToastContext.Provider>;
};

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('print', printMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  printMock.mockReset();
  show.mockReset();
  vi.unstubAllGlobals();
});

/** Fiche d'une commande : réponses GET du serveur (la commande donnée), écritures paramétrables. */
function serveDetail(
  current: ReturnType<typeof order>,
  write: (url: string) => Response = () => jsonResponse(current),
) {
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if ((init?.method ?? 'GET') !== 'GET') return write(u);
    if (u.includes('/events')) return jsonResponse(events);
    if (u.includes('/ticket')) return jsonResponse(ticket);
    if (u.includes('/receipt')) return jsonResponse(receipt);
    if (u.includes('/payment-methods')) return jsonResponse(PAYMENT_METHODS_FIXTURE);
    if (u.includes('/customers?')) {
      return pageOf([{ id: 'c1', code: 'CLI-000001', name: 'Awa Ouédraogo' }]);
    }
    if (u.includes('/assignees')) return jsonResponse([{ user_id: 'u2', full_name: 'Awa' }]);
    return jsonResponse(current);
  });
}

const renderDetail = (permissions: string[], route = '/restaurant/orders/o1') =>
  renderWithCapabilities(withToast(<OrderPage />), {
    permissions,
    path: '/restaurant/orders/:id',
    route,
  });

describe('commandes de restauration — suivi (R2-E)', () => {
  it('trois colonnes (reçues, en préparation, prêtes) et actions selon les permissions', async () => {
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('prep_status=RECEIVED') ? pageOf([order()]) : pageOf([]),
    );
    renderWithCapabilities(<OrdersPage />, { permissions: [VIEW] });
    const received = await screen.findByRole('region', { name: 'Reçues' });
    expect(screen.getByRole('region', { name: 'En préparation' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Prêtes' })).toBeTruthy();
    const card = await within(received).findByRole('link', { name: 'Ouvrir la commande n° 12' });
    expect(card.getAttribute('href')).toBe('/restaurant/orders/o1');
    expect(text(card)).toContain('n° 12');
    expect(text(card)).toContain('Table 4');
    expect(text(card)).toContain('Non réglée');
    const urls = calls().map(([u]) => u);
    for (const prep of ['RECEIVED', 'IN_PREPARATION', 'READY']) {
      expect(urls.some((u) => u.includes(`state=active&prep_status=${prep}`))).toBe(true);
    }
    // Consultation seule : ni saisie, ni réglages.
    expect(screen.queryByRole('button', { name: 'Nouvelle commande' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Réglages' })).toBeNull();
    expect(ORDERS_REFRESH_MS).toBeLessThanOrEqual(15_000);
  });

  it('« À régler » : commandes en cours non réglées, filtre des servies non réglées', async () => {
    fetchMock.mockImplementation(async () => pageOf([order()]));
    renderWithCapabilities(<OrdersPage />, {
      permissions: [VIEW, CREATE, 'restaurant.orders.settings.manage'],
    });
    expect(await screen.findByRole('button', { name: 'Nouvelle commande' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Réglages' })).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'À régler' }));
    await waitFor(() =>
      expect(
        calls().some(
          ([u]) => u.includes('state=active') && u.includes('settlement_status=UNSETTLED'),
        ),
      ).toBe(true),
    );
    fireEvent.click(await screen.findByLabelText('Servies non réglées seulement'));
    await waitFor(() =>
      expect(calls().some(([u]) => u.includes('unsettled_served=true'))).toBe(true),
    );
  });
});

describe('commandes de restauration — saisie (R2-E)', () => {
  const renderCreate = (permissions = [VIEW, CREATE, MENU_VIEW]) =>
    renderWithCapabilities(<OrderCreatePage />, {
      permissions,
      sites: [SITES[0] as (typeof SITES)[number]],
      path: '/restaurant/orders/new',
      route: '/restaurant/orders/new',
      extraRoutes: [{ path: '/restaurant/orders/:id', element: <p>fiche de la commande</p> }],
    });

  it('éléments commandables du menu, clé d’idempotence, ouverture de la fiche créée', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if (init?.method === 'POST') return jsonResponse(order(), 201);
      if (String(url).includes('/restaurant/menu/items')) {
        return pageOf([
          menuItem(),
          menuItem({ id: 'i3', designation: 'Bière', orderable: false, blockers: ['unavailable'] }),
        ]);
      }
      return pageOf([]);
    });
    renderCreate();
    const add = await screen.findByRole('button', { name: 'Ajouter Poulet braisé' });
    // Élément non commandable : jamais proposé.
    expect(screen.queryByRole('button', { name: 'Ajouter Bière' })).toBeNull();
    expect(calls().some(([u]) => u.includes('site_id=s1'))).toBe(true);
    fireEvent.click(add);
    fireEvent.click(add);
    expect(screen.getAllByTestId('cart-line')).toHaveLength(1);
    expect((screen.getByLabelText('Quantité de Poulet braisé') as HTMLInputElement).value).toBe(
      '2',
    );
    expect(text(screen.getByText(/Total indicatif/))).toBe(`Total indicatif : ${money('5000')}`);
    fireEvent.change(screen.getByLabelText('Note pour Poulet braisé'), {
      target: { value: 'Bien cuit' },
    });
    fireEvent.change(screen.getByLabelText(/Nom d'appel/), { target: { value: ' Table 4 ' } });
    fireEvent.click(screen.getByText('À emporter'));
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer la commande' }));
    expect(await screen.findByText('fiche de la commande')).toBeTruthy();
    const [[url, body]] = calls('POST') as [[string, Record<string, unknown>]];
    expect(url).toBe('/api/v1/restaurant/orders');
    expect(body).toEqual({
      site_id: 's1',
      service_mode: 'TAKEAWAY',
      customer_id: null,
      call_name: 'Table 4',
      lines: [{ menu_item_id: 'i1', quantity: '2', note: 'Bien cuit' }],
      idempotency_key: expect.any(String),
    });
  });

  it('refus du serveur (élément devenu non commandable) : message détaillé, saisie conservée', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if (init?.method === 'POST') {
        return jsonResponse(
          {
            status: 422,
            code: 'menu_item_not_orderable',
            items: [{ label: 'Poulet braisé', blockers: ['unavailable'] }],
          },
          422,
        );
      }
      return String(url).includes('/restaurant/menu/items') ? pageOf([menuItem()]) : pageOf([]);
    });
    renderCreate();
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter Poulet braisé' }));
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer la commande' }));
    expect(
      await screen.findByText(/Élément\(s\) non commandable\(s\) : Poulet braisé/),
    ).toBeTruthy();
    expect(screen.getAllByTestId('cart-line')).toHaveLength(1);
  });

  it('sans consultation du menu : saisie impossible, raison affichée', () => {
    fetchMock.mockImplementation(async () => pageOf([]));
    renderCreate([VIEW, CREATE]);
    expect(screen.getByText(/nécessite la consultation du menu/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Enregistrer la commande' })).toBeNull();
  });
});

describe('commandes de restauration — fiche (R2-E)', () => {
  it('numéro mis en évidence après la création, ticket 80 mm SANS prix imprimable', async () => {
    serveDetail(order());
    renderDetail(STAFF, '/restaurant/orders/o1?created=1');
    expect(text(await screen.findByTestId('order-number'))).toBe('n° 12');
    expect(screen.getByRole('heading', { name: 'Commande n° 12' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Imprimer le ticket' }));
    const dialog = await screen.findByRole('dialog');
    expect(text(await within(dialog).findByTestId('ticket-number'))).toBe('N° 12');
    const content = text(within(dialog).getByRole('article'));
    expect(content).toContain('Maquis du Lac');
    expect(content).toContain('Table 4');
    expect(content).toContain('Sur place');
    expect(within(dialog).getAllByTestId('ticket-line')).toHaveLength(2);
    // Aucun prix, aucun montant.
    expect(content).not.toMatch(/F\s?CFA|XOF|2\s?500|5\s?500|prix/i);
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Imprimer le ticket' }));
    });
    expect(printMock).toHaveBeenCalledTimes(1);
    // Page 80 mm retirée après l'impression.
    expect(document.head.querySelector('style[data-order-ticket]')).toBeNull();
    expect(document.body.classList.contains('sm-printing')).toBe(false);
  });

  it('lignes aux prix figés, historique, actions selon les permissions', async () => {
    serveDetail(order());
    renderDetail(STAFF);
    const poulet = (await screen.findByText('Poulet braisé')).closest('tr') as HTMLElement;
    expect(text(poulet)).toContain(money('2500'));
    expect(text(poulet)).toContain(money('5000'));
    expect(text(poulet)).toContain('Bien cuit');
    expect(text(screen.getByTestId('order-total'))).toBe(money('5500'));
    expect(text(await screen.findByTestId('order-event'))).toContain(
      'Commande créée — lignes 1, 2',
    );
    expect(within(poulet).getByRole('button', { name: 'Commencer' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Prendre' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Ajouter des lignes' })).toBeTruthy();
    // Règlement : permissions EXISTANTES des ventes, absentes ici.
    expect(screen.queryByRole('button', { name: 'Régler' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Réattribuer' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Tout commencer' }));
    await waitFor(() =>
      expect(calls('POST')).toEqual([['/api/v1/restaurant/orders/o1/start', null]]),
    );
  });

  it('consultation seule : aucune action d’écriture', async () => {
    serveDetail(order());
    renderDetail([VIEW]);
    await screen.findByText('Poulet braisé');
    for (const name of ['Prendre', 'Ajouter des lignes', 'Régler', 'Tout commencer', 'Commencer']) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
    expect(screen.getByRole('button', { name: 'Ticket' })).toBeTruthy();
  });

  it('paiement à la commande : préparation bloquée tant que la commande n’est pas réglée', async () => {
    serveDetail(order({ payment_timing: 'AT_ORDER' }));
    renderDetail(CASHIER);
    expect(
      await screen.findByText(/réglez cette commande avant de lancer sa préparation/),
    ).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Tout commencer' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Régler' })).toBeTruthy();
  });

  it('règlement : même dialogue que les ventes, une requête /settle idempotente, reçu ouvert', async () => {
    serveDetail(order(), (u) => {
      if (u.endsWith('/settle')) {
        return jsonResponse({ order: settledOrder(), sale_id: 'v9', replayed: false }, 201);
      }
      return jsonResponse({ code: 'not_found' }, 404);
    });
    renderDetail(CASHIER);
    fireEvent.click(await screen.findByRole('button', { name: 'Régler' }));
    const dialog = await screen.findByRole('dialog');
    const amount = (await within(dialog).findByLabelText(/^Montant reçu/)) as HTMLInputElement;
    expect(amount.value).toBe('5500');
    fireEvent.change(amount, { target: { value: '10 000' } });
    await act(async () => {
      within(dialog).getByRole('button', { name: 'Valider la vente' }).click();
    });
    await waitFor(() => expect(calls('POST')).toHaveLength(1));
    const [[url, body]] = calls('POST') as unknown as [[string, Record<string, unknown>]];
    expect(url).toBe('/api/v1/restaurant/orders/o1/settle');
    expect(body).toEqual({
      payments: [
        {
          payment_method_id: 'pm1',
          amount: null,
          amount_received: '10000',
          reference: null,
          cash_register_id: null,
        },
      ],
      credit_override: null,
      expired_lot_override: null,
      idempotency_key: expect.any(String),
    });
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({
          severity: 'success',
          summary: 'Commande réglée : vente VENT-BTQ-2026-000009.',
        }),
      ),
    );
    // Reçu construit par le serveur depuis la vente persistée.
    await waitFor(() => expect(calls().some(([u]) => u.endsWith('/sales/v9/receipt'))).toBe(true));
  });

  it('règlement refusé : message traduit dans le dialogue, nouvel essai avec la même clé', async () => {
    serveDetail(order(), () =>
      jsonResponse({ status: 409, code: 'order_settled', detail: 'x' }, 409),
    );
    renderDetail(CASHIER);
    fireEvent.click(await screen.findByRole('button', { name: 'Régler' }));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByLabelText(/^Montant reçu/);
    const submit = () =>
      act(async () => {
        within(dialog).getByRole('button', { name: 'Valider la vente' }).click();
      });
    await submit();
    expect(await within(dialog).findByText('Cette commande est déjà réglée.')).toBeTruthy();
    await submit();
    await waitFor(() => expect(calls('POST')).toHaveLength(2));
    const keys = calls('POST').map(([, b]) => (b as { idempotency_key: string }).idempotency_key);
    expect(keys[0]).toBe(keys[1]);
  });

  it('commande sans client, servie, non réglée : crédit impossible signalé (limite de V1)', async () => {
    serveDetail(
      order({
        line_counts: { received: 0, in_preparation: 0, ready: 0, served: 2, cancelled: 0 },
        lines: [line({ status: 'SERVED' })],
      }),
    );
    renderDetail(CASHIER);
    expect(await screen.findByText(/le reste dû ne peut pas être laissé à crédit/)).toBeTruthy();
  });

  it('commande réglée : état financier lu sur la vente, plus de règlement ni d’ajout', async () => {
    serveDetail(
      settledOrder({
        payment_status: 'PARTIALLY_PAID',
        amount_due: '1500.00',
        customer_id: 'c1',
        customer_name: 'Awa',
      }),
    );
    renderDetail(CASHIER);
    const sale = await screen.findByRole('link', { name: 'VENT-BTQ-2026-000009' });
    expect(sale.getAttribute('href')).toBe('/sales/v9');
    expect(text(screen.getByTestId('order-amount-due'))).toBe(money('1500'));
    expect(screen.getByText('Partiellement payée')).toBeTruthy();
    expect(screen.getByText(/aucune ligne ne peut plus être ajoutée/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Régler' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Ajouter des lignes' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Voir le reçu' })).toBeTruthy();
  });

  it('commande clôturée : figée, aucune action sur les lignes', async () => {
    serveDetail(
      settledOrder({
        status: 'CLOSED',
        prep_status: 'SERVED',
        lines: [line({ status: 'SERVED' })],
      }),
    );
    renderDetail(CASHIER);
    expect(await screen.findByText(/Commande clôturée/)).toBeTruthy();
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
    for (const name of ['Prendre', 'Régler', 'Annuler la commande']) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
  });
});

describe('commandes de restauration — réglages (R2-E)', () => {
  it('réglages d’un site : valeurs du serveur, enregistrement des seules valeurs modifiables', async () => {
    const settings = {
      site_id: 's1',
      payment_timing: 'AT_END',
      claim_protection_minutes: 5,
      claim_cooldown_minutes: 0,
      qr_auto_accept: false,
      updated_at: '2026-10-10T08:00:00Z',
    };
    fetchMock.mockImplementation(async (_url, init) =>
      jsonResponse(init?.method === 'PUT' ? { ...settings, payment_timing: 'AT_ORDER' } : settings),
    );
    renderWithCapabilities(withToast(<OrderSettingsPage />), {
      permissions: [VIEW, 'restaurant.orders.settings.manage'],
      sites: [SITES[0] as (typeof SITES)[number]],
    });
    const protection = (await screen.findByLabelText(/Protection/)) as HTMLInputElement;
    expect(protection.value).toBe('5 min');
    expect(calls()[0]?.[0]).toBe('/api/v1/restaurant/settings/s1');
    expect(screen.getByText(/Acceptation automatique des commandes QR/)).toBeTruthy();
    fireEvent.click(screen.getByText('À la commande'));
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        [
          '/api/v1/restaurant/settings/s1',
          { payment_timing: 'AT_ORDER', claim_protection_minutes: 5, claim_cooldown_minutes: 0 },
        ],
      ]),
    );
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({ severity: 'success', summary: 'Réglages enregistrés.' }),
      ),
    );
  });
});

describe('ventes issues des commandes (R2-E)', () => {
  it('canal « Restauration » libellé et proposé dans les filtres des ventes', () => {
    expect(SALE_CHANNELS).toContain('RESTAURANT');
    expect(i18next.t('sales.channels.RESTAURANT')).toBe('Restauration');
  });
});

describe('commandes de restauration — association tardive du client (R2-E, Z1)', () => {
  const SLOW = { timeout: 5000 };
  const WITH_CUSTOMERS = [...CASHIER, 'customers.customer.view'];

  async function pickCustomer(dialog: HTMLElement) {
    const input = within(dialog).getByRole('combobox') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'awa' } });
    await waitFor(() => expect(input.getAttribute('aria-expanded')).toBe('true'), SLOW);
    const list = document.getElementById(input.getAttribute('aria-controls') ?? '') as HTMLElement;
    fireEvent.click(
      await within(list).findByRole('option', { name: /CLI-000001/, hidden: true }, SLOW),
    );
    await waitFor(() => expect(input.value).toMatch(/Awa/), SLOW);
  }

  it('commande servie sans client : association, puis règlement à crédit possible', async () => {
    const served = order({
      line_counts: { received: 0, in_preparation: 0, ready: 0, served: 2, cancelled: 0 },
      lines: [line({ status: 'SERVED' })],
    });
    serveDetail(served, () => jsonResponse({ ...served, customer_id: 'c1', customer_name: 'Awa' }));
    renderDetail(WITH_CUSTOMERS);
    expect(await screen.findByText(/Associez un client pour un règlement à crédit/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Associer un client' }));
    const dialog = await screen.findByRole('dialog');
    // Aucun motif demandé pour une première association.
    expect(within(dialog).queryByLabelText(/^Motif/)).toBeNull();
    const save = within(dialog).getByRole('button', { name: 'Enregistrer' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    await pickCustomer(dialog);
    await act(async () => {
      fireEvent.click(save);
    });
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        ['/api/v1/restaurant/orders/o1/customer', { customer_id: 'c1', reason: null }],
      ]),
    );
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({ severity: 'success', summary: 'Client associé à la commande.' }),
      ),
    );
  });

  it('remplacement : motif obligatoire, refus du serveur affiché dans le dialogue', async () => {
    serveDetail(order({ customer_id: 'c0', customer_name: 'Moussa' }), () =>
      jsonResponse({ status: 409, code: 'order_settled', detail: 'x' }, 409),
    );
    renderDetail(WITH_CUSTOMERS);
    fireEvent.click(await screen.findByRole('button', { name: 'Changer de client' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toContain('Client actuel : Moussa');
    await pickCustomer(dialog);
    const save = within(dialog).getByRole('button', { name: 'Enregistrer' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/^Motif/), {
      target: { value: 'Erreur de client' },
    });
    await act(async () => {
      fireEvent.click(save);
    });
    expect(await within(dialog).findByText('Cette commande est déjà réglée.')).toBeTruthy();
    expect(calls('PUT')).toEqual([
      ['/api/v1/restaurant/orders/o1/customer', { customer_id: 'c1', reason: 'Erreur de client' }],
    ]);
  });

  it('jamais proposée sans les permissions, ni sur une commande réglée', async () => {
    serveDetail(order());
    renderDetail(CASHIER); // sans consultation des clients
    await screen.findByText('Poulet braisé');
    expect(screen.queryByRole('button', { name: 'Associer un client' })).toBeNull();
    cleanup();
    serveDetail(settledOrder());
    renderDetail(WITH_CUSTOMERS);
    await screen.findByText('Poulet braisé');
    expect(screen.queryByRole('button', { name: 'Associer un client' })).toBeNull();
  });
});
