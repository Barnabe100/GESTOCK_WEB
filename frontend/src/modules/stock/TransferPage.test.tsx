// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import TransferPage from './TransferPage';

// Recherche d'article différée (AutoComplete) : marge pour une machine chargée.
const SLOW = { timeout: 5000 };

const line = (over: Record<string, unknown> = {}) => ({
  id: 'l1',
  line_no: 1,
  article_id: 'a1',
  article_reference: 'RIZ-25',
  article_designation: 'Riz 25 kg',
  unit: 'sac',
  quantity: '20.000',
  unit_cost: null,
  amount: null,
  ...over,
});

const draft = {
  id: 't1',
  number: 'TRF-000001',
  source_site_id: 's1',
  source_site_name: 'Boutique',
  destination_site_id: 's2',
  destination_site_name: 'Dépôt',
  status: 'DRAFT',
  operation_date: '2026-09-24',
  comment: null,
  total_amount: null,
  line_count: 1,
  created_at: '2026-09-24T08:00:00Z',
  created_by_name: 'Awa',
  validated_at: null,
  validated_by_name: null,
  cancelled_at: null,
  cancelled_by_name: null,
  cancellation_reason: null,
  lines: [line()],
};

const validated = {
  ...draft,
  status: 'VALIDATED',
  total_amount: '30000.00',
  validated_at: '2026-09-24T08:05:00Z',
  validated_by_name: 'Awa',
  lines: [line({ unit_cost: '1500.0000', amount: '30000.00' })],
};

const level = (articleId: string, quantity: string) => ({
  site_id: 's1',
  site_name: 'Boutique',
  article_id: articleId,
  reference: 'RIZ-25',
  designation: 'Riz 25 kg',
  unit: 'sac',
  category_name: 'Épicerie',
  article_active: true,
  quantity,
  average_cost: '1500.0000',
  stock_value: '0.00',
  min_stock: '0.000',
  max_stock: null,
  min_override: null,
  max_override: null,
  state: 'ok',
});

const FEATURES = ['stock.transfers'];

const ALL = [
  'stock.transfer.view',
  'stock.transfer.create',
  'stock.transfer.update',
  'stock.transfer.validate',
  'stock.transfer.cancel',
  'stock.level.view',
];
const MANAGER = ALL.filter((p) => p !== 'stock.transfer.cancel');

function withToast(element: ReactNode, show: ReturnType<typeof vi.fn>) {
  const ref = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const methodCalls = (fetchMock: ReturnType<typeof vi.fn<typeof fetch>>, method: string) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === method);

/** Choisit une suggestion dans la liste propre au champ (celle d'une autre ligne peut subsister). */
async function pickArticle(id: string, query: string, name: RegExp) {
  const input = document.getElementById(id) as HTMLInputElement;
  fireEvent.change(input, { target: { value: query } });
  await waitFor(() => expect(input.getAttribute('aria-expanded')).toBe('true'), SLOW);
  const list = document.getElementById(input.getAttribute('aria-controls') ?? '') as HTMLElement;
  fireEvent.click(await within(list).findByRole('option', { name, hidden: true }, SLOW));
  await waitFor(() => expect(input.value).toMatch(name), SLOW);
}

describe('saisie et consultation d’un transfert', () => {
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

  it('nouveau transfert : sites et articles obligatoires, aucun envoi sinon', async () => {
    fetchMock.mockImplementation(async () => pageOf([]));
    renderWithCapabilities(withToast(<TransferPage />, show), {
      features: FEATURES,
      permissions: ['stock.transfer.view', 'stock.transfer.create'],
      path: '/stock/transfers/new',
      route: '/stock/transfers/new',
    });
    expect(await screen.findByRole('heading', { name: 'Nouveau transfert' })).toBeTruthy();
    // Sans permission de validation : enregistrement du brouillon seulement.
    expect(screen.queryByRole('button', { name: 'Valider le transfert' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    await waitFor(() => expect(screen.getAllByText('Champ obligatoire')).toHaveLength(2));
    expect(screen.getByText('Ajoutez au moins un article.').className).toContain('p-error');
    fireEvent.click(screen.getByRole('button', { name: 'Ajouter une ligne' }));
    fireEvent.change(screen.getByLabelText('Quantité'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    expect(await screen.findByText(/Quantité invalide/)).toBeTruthy();
    expect(methodCalls(fetchMock, 'POST')).toHaveLength(0);
  });

  it('brouillon : stock disponible du site source affiché, dépassement signalé', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if (String(url).includes('/stock/levels')) return pageOf([level('a1', '15.000')]);
      return init?.method === 'PUT' ? jsonResponse(draft) : jsonResponse(draft);
    });
    renderWithCapabilities(withToast(<TransferPage />, show), {
      features: FEATURES,
      permissions: MANAGER,
      path: '/stock/transfers/:id',
      route: '/stock/transfers/t1',
    });
    expect(await screen.findByRole('heading', { name: 'Transfert TRF-000001' })).toBeTruthy();
    const available = await screen.findByTestId('available-0');
    expect(available.textContent).toBe('Stock disponible : 15 sac');
    expect(available.className).toContain('p-error'); // 20 demandés > 15 (indicatif)
    const levelsUrl = String(
      fetchMock.mock.calls.find(([url]) => String(url).includes('/stock/levels'))?.[0],
    );
    expect(levelsUrl).toContain('site_id=s1');
    expect(levelsUrl).toContain('article_id=a1');
    expect(levelsUrl).toContain('limit=200'); // taille de page maximale de l'API
    fireEvent.change(screen.getByLabelText('Quantité (sac)'), { target: { value: '10' } });
    await waitFor(() => expect(screen.getByTestId('available-0').className).toContain('sm-muted'));
    // Le site source d'un brouillon existant n'est plus modifiable.
    expect(
      (document.getElementById('transfer-source') as HTMLInputElement).closest('.p-disabled'),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    await waitFor(() => expect(methodCalls(fetchMock, 'PUT')).toHaveLength(1));
    const [url, init] = methodCalls(fetchMock, 'PUT')[0] ?? [];
    expect(String(url)).toContain('/api/v1/stock/transfers/t1');
    expect(JSON.parse(String(init?.body))).toEqual({
      destination_site_id: 's2',
      operation_date: '2026-09-24',
      comment: null,
      lines: [{ article_id: 'a1', quantity: '10' }],
    });
  });

  it('validation refusée pour stock insuffisant : message détaillé, brouillon conservé', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if (String(url).includes('/stock/levels')) return pageOf([level('a1', '15.000')]);
      if (init?.method === 'POST' && String(url).endsWith('/validate')) {
        return jsonResponse(
          {
            code: 'insufficient_stock',
            title: 'Stock insuffisant',
            status: 422,
            articles: [
              { article_id: 'a1', site_id: 's1', reference: 'RIZ-25', available: '15.000' },
            ],
          },
          422,
        );
      }
      return jsonResponse(draft);
    });
    renderWithCapabilities(withToast(<TransferPage />, show), {
      features: FEATURES,
      permissions: MANAGER,
      path: '/stock/transfers/:id',
      route: '/stock/transfers/t1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Valider le transfert' }));
    const dialog = await screen.findByRole('dialog');
    const accept = [...dialog.querySelectorAll('button')].find(
      (b) => b.textContent === 'Valider le transfert',
    );
    await act(async () => {
      accept?.click();
    });
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({
          severity: 'error',
          summary: 'Stock insuffisant : RIZ-25 (disponible : 15).',
        }),
      ),
    );
    expect(methodCalls(fetchMock, 'PUT')).toHaveLength(0);
  });

  it('ajout d’un article par recherche ; doublon refusé avant envoi', async () => {
    fetchMock.mockImplementation(async (url) => {
      if (String(url).includes('/catalog/articles')) {
        return pageOf([
          { id: 'a1', reference: 'RIZ-25', designation: 'Riz 25 kg', unit: 'sac', sale_price: '1' },
          {
            id: 'a2',
            reference: 'HUILE-5',
            designation: 'Huile 5 L',
            unit: 'bidon',
            sale_price: '1',
          },
        ]);
      }
      if (String(url).includes('/stock/levels')) return pageOf([]);
      return jsonResponse(draft);
    });
    renderWithCapabilities(withToast(<TransferPage />, show), {
      features: FEATURES,
      permissions: ALL,
      path: '/stock/transfers/:id',
      route: '/stock/transfers/t1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter une ligne' }));
    await pickArticle('line-1-article', 'huile', /HUILE-5/);
    fireEvent.click(screen.getByRole('button', { name: 'Ajouter une ligne' }));
    await pickArticle('line-2-article', 'riz', /RIZ-25/);
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

  it('transfert validé : lecture seule ; annulation réservée à la permission', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(validated));
    const view = renderWithCapabilities(withToast(<TransferPage />, show), {
      features: FEATURES,
      permissions: MANAGER,
      path: '/stock/transfers/:id',
      route: '/stock/transfers/t1',
    });
    expect(await screen.findByText('Validé')).toBeTruthy();
    expect(screen.getByText('RIZ-25 — Riz 25 kg')).toBeTruthy();
    expect(screen.getByText('Valeur transférée :')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Enregistrer le brouillon' })).toBeNull();
    // Gestionnaire : pas d'annulation.
    expect(screen.queryByRole('button', { name: 'Annuler le transfert' })).toBeNull();
    view.unmount();

    renderWithCapabilities(withToast(<TransferPage />, show), {
      features: FEATURES,
      permissions: ALL,
      path: '/stock/transfers/:id',
      route: '/stock/transfers/t1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Annuler le transfert' }));
    expect(await screen.findByText(/remises sur le site source/)).toBeTruthy();
    const confirm = screen.getAllByRole('button', { name: 'Annuler le transfert' }).at(-1);
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Motif d'annulation"), {
      target: { value: 'Erreur de destination' },
    });
    expect((confirm as HTMLButtonElement).disabled).toBe(false);
  });
  it('plan sans la fonctionnalité : consultation seule, même avec toutes les permissions', async () => {
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/stock/levels') ? pageOf([]) : jsonResponse(draft),
    );
    renderWithCapabilities(withToast(<TransferPage />, show), {
      permissions: ALL,
      path: '/stock/transfers/:id',
      route: '/stock/transfers/t1',
    });
    expect(await screen.findByText(/Consultation seule/)).toBeTruthy();
    // Brouillon historique : affiché en lecture seule, aucune opération proposée.
    expect(screen.getByText('RIZ-25 — Riz 25 kg')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Enregistrer le brouillon' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Valider le transfert' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Annuler le transfert' })).toBeNull();
  });
});
