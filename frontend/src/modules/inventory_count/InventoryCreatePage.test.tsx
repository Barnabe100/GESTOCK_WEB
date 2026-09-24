// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import InventoryCreatePage from './InventoryCreatePage';
import { ALL_PERMISSIONS, inventory } from './testData';

const SLOW = { timeout: 5000 };

function withToast(element: ReactNode) {
  const ref = { current: { show: vi.fn() } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const candidate = {
  article_id: 'a1',
  reference: 'RIZ-25',
  designation: 'Riz 25 kg',
  unit: 'sac',
  category_name: 'Épicerie',
  stocked: true,
  quantity: '40.000',
};

const posts = (fetchMock: ReturnType<typeof vi.fn<typeof fetch>>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');

describe('nouvel inventaire', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url, init) => {
      if (init?.method === 'POST') return jsonResponse(inventory({ status: 'DRAFT' }), 201);
      if (String(url).includes('stocked_only=true')) {
        return jsonResponse({ items: [candidate], total: 125, limit: 1, offset: 0 });
      }
      return pageOf([candidate]);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  const render = () =>
    renderWithCapabilities(withToast(<InventoryCreatePage />), {
      permissions: ALL_PERMISSIONS,
      path: '/inventories/new',
      route: '/inventories/new',
    });

  it('complet : aperçu du nombre d’articles du site, création sans liste d’articles', async () => {
    render();
    expect(await screen.findByRole('heading', { name: 'Nouvel inventaire' })).toBeTruthy();
    // Deux sites : le site est à choisir ; sans site, aucun envoi.
    fireEvent.click(screen.getByRole('button', { name: "Créer l'inventaire" }));
    expect(await screen.findByText('Champ obligatoire')).toBeTruthy();
    expect(posts(fetchMock)).toHaveLength(0);
    fireEvent.click(document.querySelector('#inventory-site')?.closest('.p-dropdown') as Element);
    fireEvent.click(await screen.findByRole('option', { name: 'Boutique', hidden: true }, SLOW));
    expect(await screen.findByText(/125 article\(s\) actif\(s\)/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: "Créer l'inventaire" }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    expect(JSON.parse(String(posts(fetchMock)[0]?.[1]?.body))).toEqual({
      site_id: 's1',
      inventory_type: 'FULL',
      article_ids: [],
      comment: null,
    });
  });

  it('ciblé : recherche serveur, sélection, retrait, articles obligatoires', async () => {
    render();
    await screen.findByRole('heading', { name: 'Nouvel inventaire' });
    fireEvent.click(document.querySelector('#inventory-site')?.closest('.p-dropdown') as Element);
    fireEvent.click(await screen.findByRole('option', { name: 'Boutique', hidden: true }, SLOW));
    fireEvent.click(screen.getByLabelText(/Ciblé/));
    fireEvent.click(screen.getByRole('button', { name: "Créer l'inventaire" }));
    expect(await screen.findByText('Choisissez au moins un article')).toBeTruthy();
    expect(posts(fetchMock)).toHaveLength(0);

    const input = document.getElementById('inventory-article') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'riz' } });
    await waitFor(() => expect(input.getAttribute('aria-expanded')).toBe('true'), SLOW);
    const list = document.getElementById(input.getAttribute('aria-controls') ?? '') as HTMLElement;
    fireEvent.click(
      await within(list).findByRole('option', { name: /RIZ-25/, hidden: true }, SLOW),
    );
    const searches = fetchMock.mock.calls
      .map(([u]) => String(u))
      .filter((u) => u.includes('search=riz'));
    expect(searches[0]).toContain('/inventories/candidates?site_id=s1');
    const row = (await screen.findByText('Riz 25 kg')).closest('tr') as HTMLElement;
    expect(within(row).getByText('40 sac')).toBeTruthy();
    // Retrait puis nouvel ajout.
    fireEvent.click(within(row).getByRole('button', { name: "Retirer l'article" }));
    expect(await screen.findByText('Aucun article choisi.')).toBeTruthy();
    fireEvent.change(input, { target: { value: 'riz' } });
    await waitFor(() => expect(input.getAttribute('aria-expanded')).toBe('true'), SLOW);
    const again = document.getElementById(input.getAttribute('aria-controls') ?? '') as HTMLElement;
    fireEvent.click(
      await within(again).findByRole('option', { name: /RIZ-25/, hidden: true }, SLOW),
    );
    expect(await screen.findByText('Riz 25 kg')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: "Créer l'inventaire" }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    expect(JSON.parse(String(posts(fetchMock)[0]?.[1]?.body))).toMatchObject({
      inventory_type: 'TARGETED',
      article_ids: ['a1'],
    });
  });
});
