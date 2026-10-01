// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import SalePage from './SalePage';
import { PAYMENT_METHODS_FIXTURE } from './testData';

// Espaces insécables normalisés comme le fait Testing Library.
const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s/g, ' ');

const line = (over: Record<string, unknown> = {}) => ({
  id: 'l1',
  line_no: 1,
  article_id: 'a1',
  article_reference: 'VIS-001',
  article_designation: 'Vis à bois',
  unit: 'boîte',
  quantity: '2.000',
  unit_price: '1500.00',
  line_total: '3000.00',
  ...over,
});

const draft = {
  id: 'v1',
  // Brouillon : aucun numéro (attribué à la validation).
  number: null,
  site_id: 's1',
  site_name: 'Boutique',
  customer_id: null,
  customer_code: null,
  customer_name: null,
  status: 'DRAFT',
  sale_date: '2026-09-24',
  notes: null,
  subtotal: '3000.00',
  total: '3000.00',
  line_count: 1,
  created_at: '2026-09-24T08:00:00Z',
  updated_at: '2026-09-24T08:00:00Z',
  created_by_name: 'Moussa',
  validated_at: null,
  validated_by_name: null,
  cancelled_at: null,
  cancelled_by_name: null,
  cancellation_reason: null,
  is_credit: false,
  credit_status: null,
  credit_override_at: null,
  credit_override_by_name: null,
  credit_override_reason: null,
  credit_override_amount: null,
  lines: [line()],
};

const withCustomer = {
  ...draft,
  customer_id: 'c1',
  customer_code: 'CLI-000001',
  customer_name: 'Awa Ouédraogo',
};

const validated = {
  ...draft,
  number: 'VENT-BOU-2026-000001',
  status: 'VALIDATED',
  customer_id: 'c1',
  customer_code: 'CLI-000001',
  customer_name: 'Awa Ouédraogo',
  validated_at: '2026-09-24T08:05:00Z',
  validated_by_name: 'Moussa',
};

// Recherche d'article différée (AutoComplete) : marge pour une machine chargée.
const SLOW = { timeout: 5000 };

const ALL = [
  'sales.sale.view',
  'sales.sale.create',
  'sales.sale.update',
  'sales.sale.validate',
  'sales.sale.cancel',
];
const SELLER = ALL.filter((p) => p !== 'sales.sale.cancel');

function withToast(element: ReactNode, show: ReturnType<typeof vi.fn>) {
  const ref = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

/** Choisit une suggestion dans la liste propre au champ (celle d'une autre ligne peut subsister). */
async function pickArticle(id: string, query: string, name: RegExp) {
  const input = document.getElementById(id) as HTMLInputElement;
  fireEvent.change(input, { target: { value: query } });
  await waitFor(() => expect(input.getAttribute('aria-expanded')).toBe('true'), SLOW);
  const list = document.getElementById(input.getAttribute('aria-controls') ?? '') as HTMLElement;
  fireEvent.click(await within(list).findByRole('option', { name, hidden: true }, SLOW));
  await waitFor(() => expect(input.value).toMatch(name), SLOW);
}

const methodCalls = (fetchMock: ReturnType<typeof vi.fn<typeof fetch>>, method: string) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === method);

describe('saisie et consultation d’une vente', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const show = vi.fn();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  it('nouvelle vente : site obligatoire, client facultatif, au moins un article', async () => {
    fetchMock.mockImplementation(async () => pageOf([]));
    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: ['sales.sale.view', 'sales.sale.create'],
      path: '/sales/new',
      route: '/sales/new',
    });
    expect(await screen.findByRole('heading', { name: 'Nouvelle vente' })).toBeTruthy();
    expect(screen.getByPlaceholderText('Client ordinaire (sans client)')).toBeTruthy();
    // Sans permission de validation : enregistrement du brouillon seulement.
    expect(screen.queryByRole('button', { name: 'Valider la vente' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    expect(await screen.findByText('Champ obligatoire')).toBeTruthy(); // site
    expect(screen.getByText('Ajoutez au moins un article.').className).toContain('p-error');
    // Ligne ajoutée sans article : refusée avant tout envoi.
    fireEvent.click(screen.getByRole('button', { name: 'Ajouter une ligne' }));
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    // Site et article manquants.
    await waitFor(() => expect(screen.getAllByText('Champ obligatoire')).toHaveLength(2));
    expect(methodCalls(fetchMock, 'POST')).toHaveLength(0);
  });

  it('brouillon : total recalculé à la saisie, prix jamais envoyé au serveur', async () => {
    fetchMock.mockImplementation(async (_url, init) =>
      init?.method === 'PUT'
        ? jsonResponse({ ...draft, lines: [line({ quantity: '3.000', line_total: '4500.00' })] })
        : jsonResponse(draft),
    );
    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: ALL,
      path: '/sales/:id',
      route: '/sales/v1',
    });
    expect(await screen.findByRole('heading', { name: 'Vente non numérotée' })).toBeTruthy();
    expect(screen.getByTestId('sale-total').textContent?.replace(/\s/g, ' ')).toBe(
      money('3000.00'),
    );
    expect(screen.getByText(`Prix unitaire : ${money('1500.00')}`)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/^Quantité \(boîte\)/), { target: { value: '3' } });
    await waitFor(() =>
      expect(screen.getByTestId('sale-total').textContent?.replace(/\s/g, ' ')).toBe(
        money('4500.00'),
      ),
    );
    // Quantité décimale : 2,5 × 1 500 = 3 750.
    fireEvent.change(screen.getByLabelText(/^Quantité \(boîte\)/), { target: { value: '2,5' } });
    await waitFor(() =>
      expect(screen.getByTestId('sale-total').textContent?.replace(/\s/g, ' ')).toBe(
        money('3750.00'),
      ),
    );
    fireEvent.change(screen.getByLabelText(/^Quantité \(boîte\)/), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    await waitFor(() => expect(methodCalls(fetchMock, 'PUT')).toHaveLength(1));
    const [url, init] = methodCalls(fetchMock, 'PUT')[0] ?? [];
    expect(String(url)).toContain('/api/v1/sales/v1');
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toEqual({
      sale_date: '2026-09-24',
      customer_id: null,
      notes: null,
      lines: [{ article_id: 'a1', quantity: '3' }],
    });
    expect(JSON.stringify(body)).not.toContain('price');
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(expect.objectContaining({ severity: 'success' })),
    );
  });

  it('validation refusée pour stock insuffisant : message détaillé', async () => {
    fetchMock.mockImplementation(async (url, init) =>
      init?.method === 'POST' && String(url).endsWith('/validate')
        ? jsonResponse(
            {
              code: 'insufficient_stock',
              title: 'Stock insuffisant',
              status: 422,
              articles: [{ article_id: 'a1', reference: 'VIS-001', available: '1.000' }],
            },
            422,
          )
        : jsonResponse(draft),
    );
    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: SELLER,
      path: '/sales/:id',
      route: '/sales/v1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Valider la vente' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent?.replace(/\s/g, ' ')).toContain(money('3000.00'));
    const accept = [...dialog.querySelectorAll('button')].find(
      (b) => b.textContent === 'Valider la vente',
    );
    await act(async () => {
      accept?.click();
    });
    // Refus affiché dans le dialogue, conservé pour corriger.
    expect(
      await within(dialog).findByText('Stock insuffisant : VIS-001 (disponible : 1).'),
    ).toBeTruthy();
    // Brouillon non modifié : aucun PUT, une seule tentative de validation.
    expect(methodCalls(fetchMock, 'PUT')).toHaveLength(0);
    expect(methodCalls(fetchMock, 'POST')).toHaveLength(1);
  });

  it('sans client : encaissement exigé, montant reçu en espèces envoyé dans la même requête', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (u.includes('/payment-methods')) return jsonResponse(PAYMENT_METHODS_FIXTURE);
      return init?.method === 'POST' && u.endsWith('/validate')
        ? jsonResponse(validated)
        : jsonResponse(draft);
    });
    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: [...SELLER, 'sales.payment.create'],
      path: '/sales/:id',
      route: '/sales/v1',
    });
    expect(await screen.findByRole('heading', { name: 'Vente non numérotée' })).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: 'Valider la vente' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toContain('une vente à crédit exige un client identifié');
    // Encaissement coché d'emblée ; espèces par défaut : montant reçu = total proposé.
    expect(
      (within(dialog).getByLabelText('Encaisser un paiement maintenant') as HTMLInputElement)
        .checked,
    ).toBe(true);
    const amount = (await within(dialog).findByLabelText(/^Montant reçu/)) as HTMLInputElement;
    expect(amount.value).toBe('3000');
    fireEvent.change(amount, { target: { value: '5 000' } });
    await act(async () => {
      within(dialog).getByRole('button', { name: 'Valider la vente' }).click();
    });
    await waitFor(() => expect(methodCalls(fetchMock, 'POST')).toHaveLength(1));
    const [url, init] = methodCalls(fetchMock, 'POST')[0] ?? [];
    expect(String(url)).toContain('/api/v1/sales/v1/validate');
    expect(JSON.parse(String(init?.body))).toEqual({
      // Monnaie calculée par le serveur ; poste choisi par le serveur (aucun indiqué ici).
      payments: [
        {
          payment_method_id: 'pm1',
          amount: null,
          amount_received: '5000',
          reference: null,
          cash_register_id: null,
        },
      ],
      credit_override: null,
    });
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({
          severity: 'success',
          summary: 'Vente VENT-BOU-2026-000001 validée',
        }),
      ),
    );
  });

  it('crédit refusé sans client : message du serveur affiché dans le dialogue', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (u.includes('/payment-methods')) return jsonResponse(PAYMENT_METHODS_FIXTURE);
      return init?.method === 'POST' && u.endsWith('/validate')
        ? jsonResponse({ code: 'credit_customer_required', detail: 'x', remaining: '3000.00' }, 422)
        : jsonResponse(draft);
    });
    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: [...SELLER, 'sales.payment.create', 'sales.sale.credit_create'],
      path: '/sales/:id',
      route: '/sales/v1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Valider la vente' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByLabelText('Encaisser un paiement maintenant'));
    await act(async () => {
      within(dialog).getByRole('button', { name: 'Valider la vente' }).click();
    });
    expect(
      await within(dialog).findByText(/Une vente à crédit exige un client identifié/),
    ).toBeTruthy();
  });

  it('limite de crédit dépassée : message détaillé, dialogue conservé', async () => {
    fetchMock.mockImplementation(async (url, init) =>
      init?.method === 'POST' && String(url).endsWith('/validate')
        ? jsonResponse(
            {
              code: 'credit_limit_exceeded',
              title: 'Limite',
              status: 422,
              credit_limit: '100000.00',
              sale_exposure: '3000.00',
              override_allowed: false,
            },
            422,
          )
        : jsonResponse(withCustomer),
    );
    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: [...SELLER, 'sales.sale.credit_create'],
      path: '/sales/:id',
      route: '/sales/v1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Valider la vente' }));
    const dialog = await screen.findByRole('dialog');
    // Sans droit d'encaisser : pas d'encaissement immédiat proposé.
    expect(within(dialog).queryByLabelText('Encaisser un paiement maintenant')).toBeNull();
    expect(dialog.textContent).toContain('dans la limite de son crédit');
    await act(async () => {
      within(dialog).getByRole('button', { name: 'Valider la vente' }).click();
    });
    expect(await within(dialog).findByText(/^Limite de crédit du client dépassée/)).toBeTruthy();
    expect(JSON.parse(String(methodCalls(fetchMock, 'POST')[0]?.[1]?.body ?? 'null'))).toBeNull();
    // Dépassement non permis par le serveur à cet utilisateur : aucune justification proposée.
    expect(within(dialog).queryByLabelText(/Justification du dépassement/)).toBeNull();
  });

  it('dépassement permis par le serveur : justification obligatoire, envoyée à la validation', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if (init?.method === 'POST' && String(url).endsWith('/validate')) {
        const body = JSON.parse(String(init.body ?? 'null')) as {
          credit_override?: unknown;
        } | null;
        return body?.credit_override
          ? jsonResponse({ ...validated, is_credit: true, credit_status: 'OPEN' })
          : jsonResponse(
              {
                code: 'credit_limit_exceeded',
                detail: 'x',
                credit_limit: '1000.00',
                sale_exposure: '3000.00',
                override_allowed: true,
              },
              422,
            );
      }
      return jsonResponse(withCustomer);
    });
    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: [...ALL, 'sales.sale.credit_create', 'sales.sale.credit_override'],
      path: '/sales/:id',
      route: '/sales/v1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Valider la vente' }));
    const dialog = await screen.findByRole('dialog');
    await act(async () => {
      within(dialog).getByRole('button', { name: 'Valider la vente' }).click();
    });
    const reason = await within(dialog).findByLabelText(/Justification du dépassement/);
    const override = within(dialog).getByRole('button', {
      name: 'Autoriser le dépassement et valider',
    });
    expect((override as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(reason, { target: { value: 'Client fidèle' } });
    expect((override as HTMLButtonElement).disabled).toBe(false);
    await act(async () => {
      override.click();
    });
    await waitFor(() => expect(methodCalls(fetchMock, 'POST')).toHaveLength(2));
    expect(JSON.parse(String(methodCalls(fetchMock, 'POST')[1]?.[1]?.body))).toEqual({
      payments: [],
      credit_override: { reason: 'Client fidèle' },
    });
  });

  it('vente validée : lecture seule ; annulation réservée à la permission', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(validated));
    const view = renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: SELLER,
      path: '/sales/:id',
      route: '/sales/v1',
    });
    expect(await screen.findByText('Validée')).toBeTruthy();
    expect(screen.getByText('Awa Ouédraogo (CLI-000001)')).toBeTruthy();
    expect(screen.getByText('VIS-001 — Vis à bois')).toBeTruthy();
    expect(screen.getAllByText(money('3000.00')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Enregistrer le brouillon' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Valider la vente' })).toBeNull();
    // Vendeur : pas d'annulation.
    expect(screen.queryByRole('button', { name: 'Annuler la vente' })).toBeNull();
    view.unmount();

    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: ALL,
      path: '/sales/:id',
      route: '/sales/v1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Annuler la vente' }));
    expect(await screen.findByText(/remises en stock/)).toBeTruthy();
    const confirm = screen.getAllByRole('button', { name: 'Annuler la vente' }).at(-1);
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/^Motif d'annulation/), {
      target: { value: 'Erreur de caisse' },
    });
    expect((confirm as HTMLButtonElement).disabled).toBe(false);
  });

  it('mouvements de stock et chronologie : seulement avec la permission correspondante', async () => {
    const movement = {
      id: 'm1',
      occurred_at: '2026-09-24T08:05:00Z',
      site_id: 's1',
      site_name: 'Boutique',
      article_id: 'a1',
      article_reference: 'VIS-001',
      article_designation: 'Vis à bois',
      unit: 'boîte',
      movement_type: 'SALE',
      quantity: '-2.000',
      quantity_before: '10.000',
      quantity_after: '8.000',
      unit_cost: null,
      average_cost_before: '1000.0000',
      average_cost_after: '1000.0000',
      source_type: 'sale',
      source_id: 'v1',
      document_number: 'VENT-BOU-2026-000001',
      origin_movement_id: null,
      user_name: 'Moussa',
      comment: null,
    };
    const events = [
      {
        id: 'e1',
        occurred_at: '2026-09-24T08:00:00Z',
        action: 'sale.created',
        user_name: 'Moussa',
        data: {},
      },
      {
        id: 'e2',
        occurred_at: '2026-09-24T08:05:00Z',
        action: 'payment.created',
        user_name: 'Moussa',
        data: { number: 'PAY-000001', amount: '3000.00', method_label: 'Orange Money' },
      },
    ];
    fetchMock.mockImplementation(async (url) => {
      const path = String(url);
      if (path.includes('/stock/movements')) return pageOf([movement]);
      if (path.includes('/history')) return jsonResponse(events);
      return jsonResponse(validated);
    });
    const view = renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: ['sales.sale.view'],
      path: '/sales/:id',
      route: '/sales/v1',
    });
    expect(await screen.findByText('Validée')).toBeTruthy();
    expect(screen.queryByText('Mouvements de stock')).toBeNull();
    expect(screen.queryByText('Chronologie')).toBeNull();
    expect(fetchMock.mock.calls.some(([u]) => /stock\/movements|\/history/.test(String(u)))).toBe(
      false,
    );
    view.unmount();

    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: ['sales.sale.view', 'stock.movement.view', 'audit.log.view'],
      path: '/sales/:id',
      route: '/sales/v1',
    });
    expect(await screen.findByText('Mouvements de stock')).toBeTruthy();
    expect(await screen.findByText('Vente', { selector: 'td' })).toBeTruthy();
    const call = fetchMock.mock.calls.map(([u]) => String(u)).find((u) => u.includes('/stock/'));
    expect(call).toContain('source_type=sale');
    expect(call).toContain('source_id=v1');
    expect(await screen.findByText('Chronologie')).toBeTruthy();
    expect(await screen.findByText('Vente créée (brouillon)')).toBeTruthy();
    expect(screen.getByText('Paiement enregistré')).toBeTruthy();
    expect(
      screen.getByText(new RegExp(`PAY-000001 — ${money('3000.00')} — Orange Money`)),
    ).toBeTruthy();
  });

  it('brouillon consulté sans droit de modification : résumé en lecture seule', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(draft));
    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: ['sales.sale.view'],
      path: '/sales/:id',
      route: '/sales/v1',
    });
    expect(await screen.findByText('Brouillon')).toBeTruthy();
    expect(screen.getByText('Ordinaire')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Enregistrer le brouillon' })).toBeNull();
  });
  it('ajout d’un article par recherche : prix repris, doublon refusé', async () => {
    const article = (id: string, reference: string, price: string) => ({
      id,
      reference,
      designation: `Article ${reference}`,
      unit: 'pièce',
      sale_price: price,
      is_active: true,
    });
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/catalog/articles')
        ? pageOf([article('a1', 'VIS-001', '1500.00'), article('a2', 'CLOU-02', '250.00')])
        : jsonResponse(draft),
    );
    renderWithCapabilities(withToast(<SalePage />, show), {
      permissions: ALL,
      path: '/sales/:id',
      route: '/sales/v1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter une ligne' }));
    await pickArticle('line-1-article', 'clou', /CLOU-02/);
    // Quantité 1 par défaut × 250 : total 3 000 + 250.
    await waitFor(
      () =>
        expect(screen.getByTestId('sale-total').textContent?.replace(/\s/g, ' ')).toBe(
          money('3250.00'),
        ),
      SLOW,
    );
    // Troisième ligne sur le même article que la première : refusée avant envoi.
    fireEvent.click(screen.getByRole('button', { name: 'Ajouter une ligne' }));
    await pickArticle('line-2-article', 'vis', /VIS-001/);
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    expect(
      await screen.findByText(
        "Un même article ne peut figurer qu'une fois dans le document.",
        undefined,
        SLOW,
      ),
    ).toBeTruthy();
    expect(methodCalls(fetchMock, 'PUT')).toHaveLength(0);
  }, 20_000);
});
