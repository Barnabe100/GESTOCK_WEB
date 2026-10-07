// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities, SITES } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import SalePage from './SalePage';

/**
 * Brouillons de vente au back-office : un seul brouillon créé, modification enregistrée avant
 * la validation, validation de la dernière version enregistrée, une seule soumission à la fois
 * (même mécanisme que les documents de stock : `useDraftSubmission`).
 */

// Recherche d'article différée (AutoComplete) : marge pour une machine chargée.
const SLOW = { timeout: 5000 };

const ARTICLE = {
  id: 'a1',
  reference: 'VIS-001',
  designation: 'Vis à bois',
  unit: 'boîte',
  sale_price: '1500.00',
  is_active: true,
};

const saleLine = (index: number, input: { article_id: string; quantity: string }) => {
  const quantity = Number(input.quantity).toFixed(3);
  return {
    id: `l${index + 1}`,
    line_no: index + 1,
    article_id: input.article_id,
    article_reference: ARTICLE.reference,
    article_designation: ARTICLE.designation,
    unit: ARTICLE.unit,
    packaging_id: null,
    packaging_name: null,
    packaging_conversion: null,
    quantity,
    unit_price: '1500.00',
    line_total: (Number(input.quantity) * 1500).toFixed(2),
  };
};

const sale = (over: Record<string, unknown> = {}) => ({
  id: 'v1',
  number: null,
  site_id: 's1',
  site_name: 'Boutique',
  customer_id: null,
  customer_code: null,
  customer_name: null,
  status: 'DRAFT',
  sale_date: '2026-10-07',
  notes: null,
  subtotal: '3000.00',
  total: '3000.00',
  line_count: 1,
  created_at: '2026-10-07T08:00:00Z',
  updated_at: '2026-10-07T08:00:00Z',
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
  lines: [saleLine(0, { article_id: 'a1', quantity: '2' })],
  ...over,
});

const REFUSAL = {
  code: 'insufficient_stock',
  title: 'Stock insuffisant',
  status: 422,
  articles: [{ article_id: 'a1', reference: 'VIS-001', available: '1.000' }],
};

const fetchMock = vi.fn<typeof fetch>();
/** Appels d'écriture dans l'ordre : « POST /sales », « PUT /sales/v1 »… */
const writes = () =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method && init.method !== 'GET')
    .map(([url, init]) => `${init?.method} ${String(url).replace(/^.*\/api\/v1/, '')}`);
const bodies = (call: string) =>
  fetchMock.mock.calls
    .filter(([url, init]) => `${init?.method} ${String(url).replace(/^.*\/api\/v1/, '')}` === call)
    .map(([, init]) => JSON.parse(String(init?.body)) as { lines: { quantity: string }[] });

/**
 * Serveur simulé : création, mise à jour, validation (les `refusals` premières refusées pour
 * stock insuffisant). Une validation enregistre la version du brouillon qu'elle a validée.
 */
function saleServer(initial: ReturnType<typeof sale> | null, refusals = 0) {
  let current = initial;
  let refused = refusals;
  const validatedVersions: string[] = [];
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url).replace(/\?.*$/, '');
    const method = init?.method ?? 'GET';
    if (u.endsWith('/catalog/articles')) return pageOf([ARTICLE]);
    const body = init?.body
      ? (JSON.parse(String(init.body)) as {
          lines: { article_id: string; quantity: string }[];
          notes: string | null;
        })
      : null;
    if (method === 'POST' && u.endsWith('/sales') && body) {
      current = sale({ notes: body.notes, lines: body.lines.map((l, i) => saleLine(i, l)) });
      return jsonResponse(current, 201);
    }
    if (method === 'PUT' && u.endsWith('/sales/v1') && body) {
      current = sale({ notes: body.notes, lines: body.lines.map((l, i) => saleLine(i, l)) });
      return jsonResponse(current);
    }
    if (method === 'POST' && u.endsWith('/sales/v1/validate')) {
      if (refused > 0) {
        refused -= 1;
        return jsonResponse(REFUSAL, 422);
      }
      validatedVersions.push(current?.lines[0]?.quantity ?? '');
      current = sale({
        ...current,
        status: 'VALIDATED',
        number: 'VENT-BOU-2026-000001',
        validated_at: '2026-10-07T09:00:00Z',
        validated_by_name: 'Moussa',
      });
      return jsonResponse(current);
    }
    if (u.endsWith('/sales/v1')) return jsonResponse(current);
    return pageOf([]);
  });
  return { validatedVersions };
}

function withToast(element: ReactNode) {
  const ref = { current: { show: vi.fn() } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const PERMISSIONS = [
  'sales.sale.view',
  'sales.sale.create',
  'sales.sale.update',
  'sales.sale.validate',
];

/** Nouvelle vente ; `path` « /sales/* » : formulaire « nouveau » encore affiché après création. */
function renderNew(path = '/sales/*') {
  renderWithCapabilities(withToast(<SalePage />), {
    permissions: PERMISSIONS,
    sites: [SITES[0]!],
    path,
    route: '/sales/new',
    extraRoutes:
      path === '/sales/new' ? [{ path: '/sales/:id', element: withToast(<SalePage />) }] : [],
  });
}

function renderDraft() {
  renderWithCapabilities(withToast(<SalePage />), {
    permissions: PERMISSIONS,
    sites: [SITES[0]!],
    path: '/sales/:id',
    route: '/sales/v1',
  });
}

/** Ajoute l'article (quantité 1 par défaut) puis fixe la quantité. */
async function addArticle(quantity: string) {
  await screen.findByRole('heading', { name: 'Nouvelle vente' });
  fireEvent.click(screen.getByRole('button', { name: 'Ajouter une ligne' }));
  const input = document.getElementById('line-0-article') as HTMLInputElement;
  fireEvent.change(input, { target: { value: 'vis' } });
  await waitFor(() => expect(input.getAttribute('aria-expanded')).toBe('true'), SLOW);
  const list = document.getElementById(input.getAttribute('aria-controls') ?? '') as HTMLElement;
  fireEvent.click(await within(list).findByRole('option', { name: /VIS-001/, hidden: true }, SLOW));
  await waitFor(() => expect(input.value).toMatch(/VIS-001/), SLOW);
  setQuantity(quantity);
}

const setQuantity = (value: string) =>
  fireEvent.change(screen.getByLabelText(/^Quantité \(boîte\)/), { target: { value } });

const saveButton = () => screen.getByRole('button', { name: 'Enregistrer le brouillon' });

/** « Valider la vente » sur la page : ouvre le dialogue de confirmation. */
async function openValidation() {
  fireEvent.click(screen.getAllByRole('button', { name: 'Valider la vente' })[0]!);
  return screen.findByRole('dialog');
}

const confirmButton = (dialog: HTMLElement) =>
  within(dialog).getByRole('button', { name: 'Valider la vente' });

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe('brouillons de vente (back-office)', () => {
  it('brouillon créé puis « Valider la vente » aussitôt : un seul brouillon, validé par son identifiant', async () => {
    saleServer(null);
    renderNew();
    await addArticle('2');
    fireEvent.click(saveButton());
    // Validation demandée sans attendre la fin de l'enregistrement.
    const dialog = await openValidation();
    await waitFor(() => expect(writes()).toEqual(['POST /sales']));
    await act(async () => {
      fireEvent.click(confirmButton(dialog));
    });
    await waitFor(() => expect(writes()).toEqual(['POST /sales', 'POST /sales/v1/validate']));
  });

  it('double clic sur « Enregistrer le brouillon » : une seule création', async () => {
    saleServer(null);
    renderNew();
    await addArticle('2');
    fireEvent.click(saveButton());
    fireEvent.click(saveButton());
    await waitFor(() => expect(writes()).toEqual(['POST /sales']));
    // Un nouvel enregistrement met à jour CE brouillon.
    setQuantity('3');
    await act(async () => {
      fireEvent.click(saveButton());
    });
    await waitFor(() => expect(writes()).toEqual(['POST /sales', 'PUT /sales/v1']));
  });

  it('brouillon modifié puis validé : la modification est enregistrée, puis cette version validée', async () => {
    const server = saleServer(sale());
    renderDraft();
    await screen.findByRole('heading', { name: 'Vente non numérotée' });
    setQuantity('3');
    const dialog = await openValidation();
    await act(async () => {
      fireEvent.click(confirmButton(dialog));
    });
    await waitFor(() => expect(writes()).toEqual(['PUT /sales/v1', 'POST /sales/v1/validate']));
    expect(bodies('PUT /sales/v1')[0]?.lines[0]?.quantity).toBe('3');
    expect(server.validatedVersions).toEqual(['3.000']);
  });

  it('validation refusée → modification → nouvelle validation : version corrigée enregistrée et validée', async () => {
    const server = saleServer(sale(), 1);
    renderDraft();
    await screen.findByRole('heading', { name: 'Vente non numérotée' });
    let dialog = await openValidation();
    await act(async () => {
      fireEvent.click(confirmButton(dialog));
    });
    expect(
      await within(dialog).findByText('Stock insuffisant : VIS-001 (disponible : 1).'),
    ).toBeTruthy();
    // Correction de la saisie (dialogue fermé), puis nouvelle validation.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Annuler' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    setQuantity('1');
    dialog = await openValidation();
    await act(async () => {
      fireEvent.click(confirmButton(dialog));
    });
    await waitFor(() =>
      expect(writes()).toEqual([
        'POST /sales/v1/validate',
        'PUT /sales/v1',
        'POST /sales/v1/validate',
      ]),
    );
    expect(bodies('PUT /sales/v1')[0]?.lines[0]?.quantity).toBe('1');
    expect(server.validatedVersions).toEqual(['1.000']);
  });

  it('brouillon inchangé : validé sans réenregistrement', async () => {
    saleServer(sale());
    renderDraft();
    await screen.findByRole('heading', { name: 'Vente non numérotée' });
    const dialog = await openValidation();
    await act(async () => {
      fireEvent.click(confirmButton(dialog));
    });
    await waitFor(() => expect(writes()).toEqual(['POST /sales/v1/validate']));
    expect(await screen.findByRole('heading', { name: /VENT-BOU-2026-000001/ })).toBeTruthy();
  });

  it('double clic sur la confirmation : une seule validation', async () => {
    saleServer(sale());
    renderDraft();
    await screen.findByRole('heading', { name: 'Vente non numérotée' });
    setQuantity('3');
    const dialog = await openValidation();
    await act(async () => {
      fireEvent.click(confirmButton(dialog));
      fireEvent.click(confirmButton(dialog));
    });
    await waitFor(() => expect(writes()).toEqual(['PUT /sales/v1', 'POST /sales/v1/validate']));
    await screen.findByRole('heading', { name: /VENT-BOU-2026-000001/ });
    expect(writes()).toHaveLength(2);
  });

  it('nouvelle vente validée directement puis refusée : refus conservé dans le dialogue, même brouillon revalidé', async () => {
    const server = saleServer(null, 1);
    // Routes réelles : « /sales/new » puis « /sales/:id » après la création.
    renderNew('/sales/new');
    await addArticle('2');
    const dialog = await openValidation();
    await act(async () => {
      fireEvent.click(confirmButton(dialog));
    });
    expect(
      await within(dialog).findByText('Stock insuffisant : VIS-001 (disponible : 1).'),
    ).toBeTruthy();
    expect(writes()).toEqual(['POST /sales', 'POST /sales/v1/validate']);
    // Nouvelle tentative depuis le même dialogue : même brouillon, aucun second POST.
    await act(async () => {
      fireEvent.click(confirmButton(dialog));
    });
    await waitFor(() =>
      expect(writes()).toEqual([
        'POST /sales',
        'POST /sales/v1/validate',
        'POST /sales/v1/validate',
      ]),
    );
    expect(server.validatedVersions).toEqual(['2.000']);
    expect(await screen.findByRole('heading', { name: /VENT-BOU-2026-000001/ })).toBeTruthy();
  });

  it('nouvelle vente : refus puis dialogue fermé : fiche du brouillon créé, modifiable', async () => {
    saleServer(null, 1);
    renderNew('/sales/new');
    await addArticle('2');
    const dialog = await openValidation();
    await act(async () => {
      fireEvent.click(confirmButton(dialog));
    });
    await within(dialog).findByText('Stock insuffisant : VIS-001 (disponible : 1).');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Annuler' }));
    expect(await screen.findByRole('heading', { name: 'Vente non numérotée' })).toBeTruthy();
    setQuantity('1');
    await act(async () => {
      fireEvent.click(saveButton());
    });
    await waitFor(() =>
      expect(writes()).toEqual(['POST /sales', 'POST /sales/v1/validate', 'PUT /sales/v1']),
    );
  });
});
