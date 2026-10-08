// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities } from '@/shared/testing';

import ModulesPage from './ModulesPage';

const PROFILE = (code: string, name: string) => ({
  code,
  name,
  sector: { code: 'retail', name: 'Commerce de détail', icon: 'pi pi-shopping-bag' },
});
const SITES = [
  {
    id: 's1',
    name: 'Boutique',
    code: 'BTQ',
    kind: 'store',
    profile: PROFILE('retail.alimentation', 'Alimentation'),
  },
  {
    id: 's2',
    name: 'Dépôt',
    code: 'DEP',
    kind: 'warehouse',
    profile: PROFILE('retail.entrepot', 'Entrepôt'),
  },
];
const MODULE = {
  status: 'available',
  core: false,
  depends_on: [] as string[],
  in_profile: true,
  in_plan: true,
  activated_for_site: true,
  effective: true,
};
const MODULES = [
  { ...MODULE, code: 'stock' },
  { ...MODULE, code: 'pos', depends_on: ['sales'] },
  { ...MODULE, code: 'restaurant.qr', in_plan: false, activated_for_site: false, effective: false },
];

describe('modules : activation par site (palier C)', () => {
  beforeEach(() =>
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === 'PUT' ? new Response(null, { status: 204 }) : jsonResponse(MODULES),
      ),
    ),
  );
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const urls = () => vi.mocked(fetch).mock.calls.map(([url]) => String(url));

  it('lit les modules du site principal et affiche son profil', async () => {
    renderWithCapabilities(<ModulesPage />, {
      permissions: ['organization.module.view', 'organization.module.manage'],
      sites: SITES,
      mainSiteId: 's2',
    });
    expect(await screen.findByTestId('site-profile')).toHaveProperty(
      'textContent',
      expect.stringContaining('Entrepôt'),
    );
    await waitFor(() => expect(urls().some((u) => u.endsWith('/sites/s2/modules'))).toBe(true));
    expect(urls().some((u) => u.endsWith('/api/v1/modules'))).toBe(false);
  });

  it('écrit sur le site choisi, portée explicite dans l’URL', async () => {
    renderWithCapabilities(<ModulesPage />, {
      permissions: ['organization.module.view', 'organization.module.manage'],
      sites: SITES,
      mainSiteId: 's1',
    });
    const pos = await screen.findByRole('switch', { name: 'Point de vente' });
    fireEvent.click(pos);
    await waitFor(() => {
      const put = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === 'PUT');
      expect(String(put?.[0])).toMatch(/\/sites\/s1\/modules\/pos$/);
      expect(JSON.parse(String(put?.[1]?.body))).toEqual({ enabled: false });
      const headers = new Headers(put?.[1]?.headers);
      expect(headers.get('X-Site-Id')).toBeNull();
    });
  });

  it('module hors abonnement du site : non activable', async () => {
    renderWithCapabilities(<ModulesPage />, {
      permissions: ['organization.module.view', 'organization.module.manage'],
      sites: SITES,
      mainSiteId: 's1',
    });
    await screen.findByText('Non inclus dans votre offre');
    const switches = await screen.findAllByRole('switch');
    expect((switches.at(-1) as HTMLInputElement).checked).toBe(false);
    expect((switches.at(-1) as HTMLInputElement).disabled).toBe(true);
  });

  it('sans permission de gestion : lecture seule', async () => {
    renderWithCapabilities(<ModulesPage />, {
      permissions: ['organization.module.view'],
      sites: SITES,
      mainSiteId: 's1',
    });
    const switches = await screen.findAllByRole('switch');
    for (const s of switches) expect((s as HTMLInputElement).disabled).toBe(true);
  });

  it('aucun site : aucune lecture, invitation à créer un site', async () => {
    renderWithCapabilities(<ModulesPage />, {
      permissions: ['organization.module.view', 'organization.module.manage'],
      sites: [],
    });
    expect(await screen.findByText(/Créez d'abord un site/)).toBeTruthy();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});
