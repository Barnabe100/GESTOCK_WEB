// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ArticlePicker } from '@/modules/stock/ArticlePicker';
import StockLevelsPage from '@/modules/stock/StockLevelsPage';
import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { ArticleAssortmentSection } from './ArticleAssortmentSection';
import { ArticleDialog } from './ArticleDialog';
import { AssortmentNotice } from './assortment';
import AssortmentPage from './AssortmentPage';
import type { Article } from './api';

/**
 * Recette, étape 1 (ADR-0046), palier 3 : interface de l'assortiment par site. CATALOGUE ≠
 * ASSORTIMENT ≠ STOCK — le serveur décide, l'interface guide (actions selon la permission,
 * blocages du retrait détaillés à partir du `blocked` renvoyé par le serveur).
 */

const MANAGE = 'catalog.assortment.manage';
const VIEW = ['catalog.article.view', 'catalog.category.view'];
const SLOW = { timeout: 5000 };

const siteArticle = (over: Record<string, unknown> = {}) => ({
  article_id: 'a1',
  reference: 'VIS-001',
  designation: 'Vis à bois',
  category_id: 'c1',
  category_name: 'Visserie',
  unit: 'boîte',
  article_active: true,
  stock_managed: true,
  state: 'active',
  added_at: '2026-10-01T08:00:00Z',
  added_by_name: 'Awa',
  removed_at: null,
  removed_by_name: null,
  ...over,
});

const article = (over: Partial<Article> = {}): Article => ({
  id: 'a9',
  reference: 'CLOU-9',
  designation: 'Clou 9',
  category_id: 'c1',
  category_name: 'Visserie',
  unit: 'u',
  main_supplier_id: null,
  main_supplier_name: null,
  sale_price: '100.00',
  min_stock: '0.000',
  max_stock: null,
  description: null,
  barcode: null,
  is_active: true,
  stock_managed: true,
  decimal_quantity_allowed: false,
  lot_tracked: false,
  expiry_tracked: false,
  created_at: '2026-10-01T08:00:00Z',
  updated_at: '2026-10-01T08:00:00Z',
  ...over,
});

const show = vi.fn();
function withToast(element: ReactNode) {
  const ref = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const fetchMock = vi.fn<typeof fetch>();
const calls = (method: string) =>
  fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? 'GET') === method)
    .map(([u, init]) => [String(u), init?.body ? JSON.parse(String(init.body)) : null]);
const gets = (part: string) =>
  fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => u.includes(part));

const BLOCKED = {
  code: 'article_has_stock',
  detail: "Retrait impossible : l'article a encore du stock (ou un lot) sur ce site",
  site_id: 's1',
  articles: ['CLOU-2', 'VIS-001'],
  blocked: [
    { reference: 'CLOU-2', reason: 'open_document', documents: ['ENT-000004', 'VENTE-1a2b3c4d'] },
    { reference: 'VIS-001', reason: 'stock', documents: [] },
  ],
  documents: ['ENT-000004', 'VENTE-1a2b3c4d'],
  count: 2,
};

describe('assortiment par site (palier 3)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  it('page : liste du site, états, ajout des articles non proposés (filtre serveur)', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'POST') return jsonResponse({ added: 1, reactivated: 1, unchanged: 0 });
      if (u.includes('/catalog/sites/s1/articles')) {
        return pageOf([
          siteArticle(),
          siteArticle({
            article_id: 'a2',
            reference: 'CLOU-2',
            designation: 'Clou',
            state: 'removed',
            removed_at: '2026-10-05T08:00:00Z',
            removed_by_name: 'Moussa',
          }),
        ]);
      }
      if (u.includes('in_site_assortment=false')) {
        return pageOf([
          article(),
          article({ id: 'a2', reference: 'CLOU-2', site_assortment: 'removed' }),
        ]);
      }
      return pageOf([]);
    });
    renderWithCapabilities(withToast(<AssortmentPage />), { permissions: [...VIEW, MANAGE] });
    const row = (await screen.findByText('VIS-001')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Proposé')).toBeTruthy();
    expect(within(row).getByText(/Ajouté le .*\(Awa\)/)).toBeTruthy();
    const removed = screen.getByText('CLOU-2').closest('tr') as HTMLElement;
    expect(within(removed).getByText('Retiré')).toBeTruthy();
    expect(within(removed).getByText(/Retiré le .*\(Moussa\)/)).toBeTruthy();
    // Premier site par défaut ; liste « Proposés » par défaut (filtre serveur).
    expect(gets('/catalog/sites/s1/articles')[0]).toContain('status=active');

    fireEvent.click(screen.getByRole('button', { name: 'Ajouter des articles' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('CLOU-9')).toBeTruthy();
    // Seuls les articles pas encore proposés par CE site sont demandés au serveur.
    const query = gets('in_site_assortment=false')[0] ?? '';
    expect(query).toContain('site_id=s1');
    expect(query).toContain('status=active');
    const add = within(dialog).getByRole('button', { name: /Ajouter 0 article/ });
    expect((add as HTMLButtonElement).disabled).toBe(true);
    for (const reference of ['CLOU-9', 'CLOU-2']) {
      const line = within(dialog).getByText(reference).closest('tr') as HTMLElement;
      fireEvent.click(line.querySelector('input.p-checkbox-input') as Element);
    }
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ajouter 2 articles' }));
    await waitFor(() =>
      expect(calls('POST')).toEqual([
        ['/api/v1/catalog/sites/s1/articles', { article_ids: ['a9', 'a2'] }],
      ]),
    );
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({ summary: '1 ajouté(s), 1 réactivé(s), 0 déjà présent(s)' }),
      ),
    );
  });

  it('retrait refusé : chaque article bloquant et sa raison, jamais le message générique', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if (init?.method === 'POST') return jsonResponse(BLOCKED, 409);
      if (String(url).includes('/catalog/sites/')) {
        return pageOf([siteArticle(), siteArticle({ article_id: 'a2', reference: 'CLOU-2' })]);
      }
      return pageOf([]);
    });
    renderWithCapabilities(withToast(<AssortmentPage />), { permissions: [...VIEW, MANAGE] });
    await screen.findByText('VIS-001');
    // Retrait groupé de la sélection : tout ou rien.
    for (const reference of ['VIS-001', 'CLOU-2']) {
      const line = screen.getByText(reference).closest('tr') as HTMLElement;
      fireEvent.click(line.querySelector('input.p-checkbox-input') as Element);
    }
    expect(screen.getByText('2 articles sélectionnés')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retirer la sélection' }));
    await screen.findByRole('dialog', {}, SLOW);
    // Acceptation du dialogue de confirmation (dernier bouton portant ce nom).
    fireEvent.click(
      screen.getAllByRole('button', { name: "Retirer de l'assortiment" }).at(-1) as Element,
    );
    await waitFor(() => expect(calls('POST')).toHaveLength(1));
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText(/Aucun article n'a été retiré/)).toBeTruthy();
    const items = within(alert).getAllByRole('listitem');
    expect(items[0]?.textContent).toContain('CLOU-2');
    expect(items[0]?.textContent).toContain('ENT-000004, VENTE-1a2b3c4d');
    expect(items[1]?.textContent).toContain('VIS-001');
    expect(items[1]?.textContent).toContain('du stock (ou un solde de lot) reste sur ce site');
    // Le message générique (changement de suivi, article non géré) n'est jamais affiché.
    expect(screen.queryByText(/le passer en article non géré/)).toBeNull();
    expect(show).not.toHaveBeenCalled();
    expect(calls('POST')).toEqual([
      ['/api/v1/catalog/sites/s1/articles/remove', { article_ids: ['a1', 'a2'] }],
    ]);
  });

  it('réactivation et copie depuis un autre site', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if (init?.method === 'POST') return jsonResponse({ added: 3, reactivated: 0, unchanged: 1 });
      if (String(url).includes('/catalog/sites/')) {
        return pageOf([siteArticle({ state: 'removed', removed_at: '2026-10-05T08:00:00Z' })]);
      }
      return pageOf([{ id: 'c1', name: 'Visserie', is_active: true }]);
    });
    renderWithCapabilities(withToast(<AssortmentPage />), { permissions: [...VIEW, MANAGE] });
    const row = (await screen.findByText('VIS-001')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Réactiver' }));
    await waitFor(() =>
      expect(calls('POST')[0]).toEqual([
        '/api/v1/catalog/sites/s1/articles',
        { article_ids: ['a1'] },
      ]),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Copier depuis un site' }));
    const dialog = await screen.findByRole('dialog');
    const copy = within(dialog).getByRole('button', { name: 'Copier depuis un site' });
    expect((copy as HTMLButtonElement).disabled).toBe(true); // site source obligatoire
    const source = document.querySelector('#copy-source')?.closest('.p-dropdown') as Element;
    fireEvent.click(source);
    // Le site lui-même n'est jamais proposé comme source.
    await screen.findAllByRole('option', { name: 'Dépôt', hidden: true }, SLOW);
    const items = [...document.querySelectorAll('.p-dropdown-panel .p-dropdown-item')];
    expect(items.map((i) => i.textContent)).toEqual(['Dépôt']);
    fireEvent.click(items[0] as Element);
    fireEvent.click(copy);
    await waitFor(() =>
      expect(calls('POST')[1]).toEqual([
        '/api/v1/catalog/sites/s1/articles/copy',
        { source_site_id: 's2', category_id: null },
      ]),
    );
  });

  it('sans la permission : consultation seule, aucune action', async () => {
    fetchMock.mockImplementation(async () => pageOf([siteArticle()]));
    renderWithCapabilities(withToast(<AssortmentPage />), { permissions: VIEW });
    const row = (await screen.findByText('VIS-001')).closest('tr') as HTMLElement;
    expect(screen.queryByRole('button', { name: 'Ajouter des articles' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Copier depuis un site' })).toBeNull();
    expect(within(row).queryAllByRole('checkbox')).toHaveLength(0);
    expect(within(row).queryByRole('button', { name: "Retirer de l'assortiment" })).toBeNull();
  });

  it('fiche article : section Sites (état par site, ajout, blocage détaillé)', async () => {
    fetchMock.mockImplementation(async (_url, init) => {
      if (init?.method === 'POST' && String(_url).endsWith('/remove')) {
        return jsonResponse({ ...BLOCKED, blocked: [BLOCKED.blocked[1]] }, 409);
      }
      if (init?.method === 'POST') return jsonResponse({ added: 1, reactivated: 0, unchanged: 0 });
      return jsonResponse([
        {
          site_id: 's1',
          site_name: 'Boutique',
          state: 'active',
          added_at: '2026-10-01T08:00:00Z',
          removed_at: null,
        },
        { site_id: 's2', site_name: 'Dépôt', state: 'none', added_at: null, removed_at: null },
      ]);
    });
    renderWithCapabilities(withToast(<ArticleAssortmentSection article={article()} />), {
      permissions: [...VIEW, MANAGE],
    });
    const shop = (await screen.findByText('Boutique')).closest('tr') as HTMLElement;
    expect(within(shop).getByText('Proposé')).toBeTruthy();
    const depot = screen.getByText('Dépôt').closest('tr') as HTMLElement;
    expect(within(depot).getByText('Non proposé')).toBeTruthy();
    fireEvent.click(within(depot).getByRole('button', { name: "Ajouter à l'assortiment" }));
    await waitFor(() =>
      expect(calls('POST')[0]).toEqual([
        '/api/v1/catalog/sites/s2/articles',
        { article_ids: ['a9'] },
      ]),
    );
    const shopRow = (await screen.findByText('Boutique')).closest('tr') as HTMLElement;
    fireEvent.click(within(shopRow).getByRole('button', { name: "Retirer de l'assortiment" }));
    await screen.findByRole('dialog', {}, SLOW);
    // Acceptation du dialogue de confirmation (dernier bouton portant ce nom).
    fireEvent.click(
      screen.getAllByRole('button', { name: "Retirer de l'assortiment" }).at(-1) as Element,
    );
    const alert = await screen.findByRole('alert');
    expect(
      within(alert).getByText(/du stock \(ou un solde de lot\) reste sur ce site/),
    ).toBeTruthy();
  });

  it('création d’un article : sites facultatifs, envoyés seulement s’ils sont choisis', async () => {
    fetchMock.mockImplementation(async (_url, init) => {
      if (init?.method === 'POST') return jsonResponse(article(), 201);
      return pageOf([{ id: 'c1', name: 'Visserie', is_active: true }]);
    });
    const permissions = [...VIEW, 'catalog.article.create', 'catalog.article.price_update', MANAGE];
    renderWithCapabilities(withToast(<ArticleDialog article={null} onClose={() => undefined} />), {
      permissions,
    });
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Proposer sur les sites')).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/Référence/), { target: { value: 'N-1' } });
    fireEvent.change(within(dialog).getByLabelText(/Désignation/), { target: { value: 'Neuf' } });
    fireEvent.change(within(dialog).getByLabelText(/Unité/), { target: { value: 'u' } });
    fireEvent.click(document.querySelector('#article-category')?.closest('.p-dropdown') as Element);
    fireEvent.click(await screen.findByRole('option', { name: 'Visserie', hidden: true }, SLOW));
    fireEvent.click(document.querySelector('#article-sites')?.closest('.p-multiselect') as Element);
    fireEvent.click(await screen.findByRole('option', { name: 'Dépôt', hidden: true }, SLOW));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(calls('POST')).toHaveLength(1));
    expect(calls('POST')[0]?.[1]).toMatchObject({ reference: 'N-1', site_ids: ['s2'] });
  });

  it('création sans la permission d’assortiment : aucun choix de sites', async () => {
    fetchMock.mockImplementation(async () => pageOf([]));
    renderWithCapabilities(withToast(<ArticleDialog article={null} onClose={() => undefined} />), {
      permissions: [...VIEW, 'catalog.article.create'],
    });
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByText('Proposer sur les sites')).toBeNull();
  });

  it('écran opérationnel : « Hors assortiment », ajout explicite ou demande à un responsable', async () => {
    const sites = [
      { site_id: 's1', site_name: 'Boutique', state: 'removed', added_at: null, removed_at: null },
      { site_id: 's2', site_name: 'Dépôt', state: 'active', added_at: null, removed_at: null },
    ];
    fetchMock.mockImplementation(async (_url, init) =>
      init?.method === 'POST'
        ? jsonResponse({ added: 0, reactivated: 1, unchanged: 0 })
        : jsonResponse(sites),
    );
    renderWithCapabilities(withToast(<AssortmentNotice articleId="a1" siteIds={['s1', 's2']} />), {
      permissions: [MANAGE],
    });
    // Source retirée : signalée ; destination proposée : rien.
    expect(await screen.findByText(/pas proposé par le site « Boutique »/)).toBeTruthy();
    expect(screen.queryByText(/« Dépôt »/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: "Ajouter à l'assortiment du site" }));
    await waitFor(() =>
      expect(calls('POST')).toEqual([
        ['/api/v1/catalog/sites/s1/articles', { article_ids: ['a1'] }],
      ]),
    );
    cleanup();
    renderWithCapabilities(withToast(<AssortmentNotice articleId="a1" siteIds={['s1']} />), {
      permissions: [],
    });
    expect(await screen.findByText(/Demandez à un responsable/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: "Ajouter à l'assortiment du site" })).toBeNull();
  });

  it('niveaux : badge « Hors assortiment », jamais de seuil, emplacement seulement à retirer', async () => {
    const level = (over: Record<string, unknown>) => ({
      site_id: 's1',
      site_name: 'Boutique',
      article_id: 'a1',
      reference: 'VIS-001',
      designation: 'Vis',
      unit: 'u',
      category_name: 'Visserie',
      article_active: true,
      quantity: '6.000',
      min_stock: '0.000',
      max_stock: null,
      min_override: null,
      max_override: null,
      state: 'ok',
      location_id: null,
      location_name: null,
      location_active: null,
      in_assortment: true,
      ...over,
    });
    fetchMock.mockImplementation(async () =>
      pageOf([
        level({}),
        level({ article_id: 'a2', reference: 'OLD-1', in_assortment: false }),
        level({
          article_id: 'a3',
          reference: 'OLD-2',
          in_assortment: false,
          location_id: 'l1',
          location_name: 'Rayon A',
          location_active: true,
        }),
      ]),
    );
    renderWithCapabilities(withToast(<StockLevelsPage />), {
      permissions: ['stock.level.view', 'stock.threshold.manage', 'stock.location.manage'],
    });
    const kept = (await screen.findByText('VIS-001')).closest('tr') as HTMLElement;
    expect(within(kept).queryByText('Hors assortiment')).toBeNull();
    expect(within(kept).getByRole('button', { name: 'Seuils du site' })).toBeTruthy();
    const old = screen.getByText('OLD-1').closest('tr') as HTMLElement;
    expect(within(old).getByText('Hors assortiment')).toBeTruthy();
    expect(within(old).queryByRole('button', { name: 'Seuils du site' })).toBeNull();
    expect(within(old).queryByRole('button', { name: 'Emplacement' })).toBeNull();
    const located = screen.getByText('OLD-2').closest('tr') as HTMLElement;
    expect(within(located).getByRole('button', { name: 'Emplacement' })).toBeTruthy();
  });

  it('sélecteur d’article : état du site demandé au serveur, « Hors assortiment » signalé', async () => {
    fetchMock.mockImplementation(async () =>
      pageOf([
        article({ id: 'a1', reference: 'VIS-001', site_assortment: 'active' }),
        article({ id: 'a2', reference: 'CLOU-2', site_assortment: 'none' }),
      ]),
    );
    renderWithCapabilities(
      <ArticlePicker id="picker" value={null} onChange={() => undefined} siteId="s1" />,
      { permissions: VIEW },
    );
    const input = document.getElementById('picker') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'v' } });
    await waitFor(() => expect(input.getAttribute('aria-expanded')).toBe('true'), SLOW);
    expect(gets('/catalog/articles?')[0]).toContain('site_id=s1');
    const list = document.getElementById(input.getAttribute('aria-controls') ?? '') as HTMLElement;
    const options = within(list).getAllByRole('option', { hidden: true });
    expect(options[0]?.textContent).toContain('VIS-001');
    expect(options[0]?.textContent).not.toContain('Hors assortiment');
    expect(options[1]?.textContent).toContain('CLOU-2');
    expect(options[1]?.textContent).toContain('Hors assortiment');
  });

  it('site par défaut : site principal du serveur, sinon premier site accessible', async () => {
    fetchMock.mockImplementation(async () => pageOf([siteArticle()]));
    renderWithCapabilities(withToast(<AssortmentPage />), { permissions: VIEW, mainSiteId: 's2' });
    await screen.findByText('VIS-001');
    expect(gets('/catalog/sites/')[0]).toContain('/catalog/sites/s2/articles');
    cleanup();
    fetchMock.mockClear();
    renderWithCapabilities(withToast(<AssortmentPage />), { permissions: VIEW });
    await screen.findByText('VIS-001');
    expect(gets('/catalog/sites/')[0]).toContain('/catalog/sites/s1/articles');
  });
});
