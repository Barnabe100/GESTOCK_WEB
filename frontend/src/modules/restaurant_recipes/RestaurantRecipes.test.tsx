// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CapabilitiesContext } from '@/core/capabilities/CapabilitiesContext';
import type { Capabilities } from '@/core/api/types';
import { SITES, jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { RECIPE_MANAGE, RECIPE_VIEW } from './api';
import { RecipeSheetDialog } from './RecipeSheetDialog';
import RecipePage from './RecipePage';
import RecipesPage from './RecipesPage';

/** Palier R8 (ADR-0053) : liste, fiche, nouvelle version, coûts (masqués), fiche de cuisine. */

const fetchMock = vi.fn<typeof fetch>();
const show = vi.fn();
const withToast = (node: ReactNode) => {
  const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={toast}>{node}</ToastContext.Provider>;
};
const requests = (method = 'GET') =>
  fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? 'GET') === method)
    .map(([url, init]) => ({
      url: String(url),
      body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null,
    }));

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  show.mockReset();
  vi.unstubAllGlobals();
});

const version = {
  id: 'v1',
  number: 1,
  yield_quantity: '4.000',
  instructions: 'Cuire le riz.',
  created_at: '2026-11-02T10:00:00Z',
  created_by_name: 'Moi',
  lines: [
    {
      article_id: 'rice',
      reference: 'RIZ',
      designation: 'Riz',
      unit: 'kg',
      quantity: '1.000',
      note: null,
      is_active: true,
      stock_managed: true,
    },
    {
      article_id: 'egg',
      reference: 'OEUF',
      designation: 'Oeuf',
      unit: 'u',
      quantity: '4.000',
      note: 'durs',
      is_active: true,
      stock_managed: true,
    },
  ],
};

const recipe = {
  id: 'r1',
  article_id: 'dish',
  reference: 'RIZGRAS',
  designation: 'Riz gras',
  unit: 'plat',
  article_active: true,
  packaging_id: null,
  packaging_name: null,
  packaging_conversion: null,
  sale_price: '1500.00',
  is_active: true,
  notes: null,
  version_count: 1,
  current_version: version,
  created_at: '2026-11-02T10:00:00Z',
  updated_at: '2026-11-02T10:00:00Z',
};

const costs = (masked: boolean) => [
  {
    site_id: 's1',
    site_name: 'Boutique',
    yield_quantity: '4.000',
    sale_price: '1500.00',
    complete: true,
    lines: [
      {
        article_id: 'rice',
        reference: 'RIZ',
        designation: 'Riz',
        unit: 'kg',
        quantity: '1.000',
        has_cost: true,
        ...(masked ? {} : { unit_cost: '800.0000', line_cost: '800.0000' }),
      },
    ],
    ...(masked
      ? {}
      : {
          batch_cost: '1440.0000',
          portion_cost: '360.0000',
          margin: '1140.00',
          margin_rate: '76.00',
        }),
  },
];

const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

describe('liste des recettes', () => {
  it('affiche les recettes du serveur ; création réservée à la gestion', async () => {
    fetchMock.mockResolvedValue(
      pageOf([
        {
          id: 'r1',
          article_id: 'dish',
          reference: 'RIZGRAS',
          designation: 'Riz gras',
          unit: 'plat',
          packaging_id: 'p1',
          packaging_name: 'Familial',
          is_active: true,
          current_number: 2,
          yield_quantity: '4.000',
          ingredient_count: 3,
          updated_at: '2026-11-02T10:00:00Z',
        },
      ]),
    );
    renderWithCapabilities(withToast(<RecipesPage />), { permissions: [RECIPE_VIEW] });
    expect(await screen.findByText('Riz gras — Familial')).toBeTruthy();
    expect(screen.getByText('v2')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Nouvelle recette' })).toBeNull();
    expect(requests()[0]!.url).toContain('/restaurant/recipes?');
    cleanup();
    renderWithCapabilities(withToast(<RecipesPage />), {
      permissions: [RECIPE_VIEW, RECIPE_MANAGE],
    });
    expect(await screen.findByRole('button', { name: 'Nouvelle recette' })).toBeTruthy();
  });
});

describe('fiche d’une recette', () => {
  const respond = (masked: boolean) =>
    fetchMock.mockImplementation(async (url, init) => {
      const path = String(url);
      if ((init?.method ?? 'GET') === 'POST' && path.endsWith('/versions')) {
        return jsonResponse({ ...recipe, version_count: 2 }, 201);
      }
      if (path.endsWith('/costs')) return jsonResponse(costs(masked));
      if (path.endsWith('/availability')) return jsonResponse([]);
      if (path.endsWith('/versions')) return jsonResponse([version]);
      return jsonResponse(recipe);
    });

  it('composition courante, coût par portion et marge (avec la permission des coûts)', async () => {
    respond(false);
    renderWithCapabilities(withToast(<RecipePage />), {
      permissions: [RECIPE_VIEW],
      path: '/restaurant/recipes/:id',
      route: '/restaurant/recipes/r1',
      sites: [SITES[0]!],
    });
    expect(await screen.findByRole('heading', { name: 'Riz gras' })).toBeTruthy();
    const table = screen.getByRole('table', { name: 'Ingrédients' });
    expect(text(table)).toContain('RIZ — Riz');
    expect(text(table)).toContain('durs');
    fireEvent.click(screen.getByRole('tab', { name: 'Coût de revient' }));
    expect(text(await screen.findByTestId('recipe-portion-cost'))).toMatch(/360/);
    expect(screen.getByText(/76/)).toBeTruthy();
    // Consultation seule : aucune action de gestion.
    expect(screen.queryByRole('button', { name: 'Modifier la composition' })).toBeNull();
  });

  it('coûts absents de la réponse : message, aucune valeur inventée', async () => {
    respond(true);
    renderWithCapabilities(withToast(<RecipePage />), {
      permissions: [RECIPE_VIEW],
      path: '/restaurant/recipes/:id',
      route: '/restaurant/recipes/r1',
    });
    fireEvent.click(await screen.findByRole('tab', { name: 'Coût de revient' }));
    expect(await screen.findByText(/réservés aux utilisateurs autorisés/)).toBeTruthy();
    expect(screen.queryByTestId('recipe-portion-cost')).toBeNull();
  });

  it('modifier la composition crée une nouvelle version (quantités normalisées)', async () => {
    respond(false);
    renderWithCapabilities(withToast(<RecipePage />), {
      permissions: [RECIPE_VIEW, RECIPE_MANAGE],
      path: '/restaurant/recipes/:id',
      route: '/restaurant/recipes/r1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Modifier la composition' }));
    expect(screen.getByText(/crée une nouvelle version/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Rendement/), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText(/Quantité \(kg\)/), { target: { value: '1,5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer une nouvelle version' }));
    await waitFor(() => expect(requests('POST')).toHaveLength(1));
    const [post] = requests('POST');
    expect(post!.url).toContain('/restaurant/recipes/r1/versions');
    expect(post!.body).toEqual({
      yield_quantity: '5',
      instructions: 'Cuire le riz.',
      lines: [
        { article_id: 'rice', quantity: '1.5', note: null },
        { article_id: 'egg', quantity: '4.000', note: 'durs' },
      ],
    });
  });

  it('erreur du serveur traduite (règle des quantités entières)', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if ((init?.method ?? 'GET') === 'POST') {
        return jsonResponse({ code: 'recipe_whole_quantity_required', articles: ['OEUF'] }, 422);
      }
      return String(url).endsWith('/versions') ? jsonResponse([version]) : jsonResponse(recipe);
    });
    renderWithCapabilities(withToast(<RecipePage />), {
      permissions: [RECIPE_VIEW, RECIPE_MANAGE],
      path: '/restaurant/recipes/:id',
      route: '/restaurant/recipes/r1',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Modifier la composition' }));
    fireEvent.change(screen.getByLabelText(/Rendement/), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer une nouvelle version' }));
    expect(await screen.findByText(/unités entières/)).toBeTruthy();
  });
});

describe('fiche technique en cuisine', () => {
  const caps = {
    tenant: { locale: 'fr', currency: 'XOF', timezone: 'UTC' },
  } as unknown as Capabilities;
  const renderSheet = () =>
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <CapabilitiesContext.Provider
          value={{
            capabilities: caps,
            can: () => true,
            isRestricted: () => false,
            hasModule: () => true,
            siteId: null,
            setSiteId: () => undefined,
          }}
        >
          <RecipeSheetDialog
            target={{ articleId: 'dish', packagingId: 'p1', quantity: '2', label: 'Riz gras' }}
            onClose={() => undefined}
          />
        </CapabilitiesContext.Provider>
      </QueryClientProvider>,
    );

  it('ingrédients mis à l’échelle par le serveur, sans aucun coût', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        recipe_id: 'r1',
        version_number: 1,
        designation: 'Riz gras',
        packaging_name: 'Familial',
        quantity: '2.000',
        portions: '8.000',
        instructions: 'Cuire.',
        lines: [
          { reference: 'RIZ', designation: 'Riz', unit: 'kg', quantity: '2.000', note: null },
        ],
      }),
    );
    renderSheet();
    const list = await screen.findByRole('list', { name: 'Ingrédients' });
    expect(text(within(list).getByRole('listitem'))).toBe('2 kg Riz');
    expect(screen.getByText(/8 portion/)).toBeTruthy();
    const [call] = requests();
    expect(call!.url).toBe(
      '/api/v1/restaurant/recipes/sheet?article_id=dish&quantity=2&packaging_id=p1',
    );
  });

  it('aucune recette : message clair', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ code: 'recipe_not_found' }, 404));
    renderSheet();
    expect(await screen.findByText('Aucune recette active pour ce plat.')).toBeTruthy();
  });
});
