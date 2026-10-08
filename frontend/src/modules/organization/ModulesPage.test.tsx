// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

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
    profile: PROFILE('distribution.entrepot', 'Entrepôt'),
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
// Site s1 : un module de chaque état.
const SITE_MODULES = [
  { ...MODULE, code: 'stock' },
  { ...MODULE, code: 'pos', depends_on: ['sales'], effective: false },
  { ...MODULE, code: 'alerts', activated_for_site: false, effective: false },
  { ...MODULE, code: 'restaurant.qr', in_plan: false, activated_for_site: false, effective: false },
  { ...MODULE, code: 'restaurant.tables', status: 'planned' },
];
// Synthèse de l'entreprise : `inventory_count` n'est proposé que par le profil d'un autre site.
const TENANT_MODULES = [
  ...SITE_MODULES.map(({ activated_for_site, ...m }) => ({ ...m, enabled: activated_for_site })),
  { ...MODULE, code: 'inventory_count', enabled: true },
];

// Refus du serveur simulé pour la prochaine écriture (le serveur est la seule frontière).
let refusal: { status: number; code: string } | null = null;
const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
  if (init?.method === 'PUT' && refusal)
    return jsonResponse({ code: refusal.code, detail: 'refus' }, refusal.status);
  if (init?.method === 'PUT') return new Response(null, { status: 204 });
  if (String(url).endsWith('/api/v1/modules')) return jsonResponse(TENANT_MODULES);
  return jsonResponse(SITE_MODULES);
});

const MANAGE = ['organization.module.view', 'organization.module.manage'];

describe('modules du site : états (paliers C et E)', () => {
  beforeEach(() => vi.stubGlobal('fetch', fetchMock));
  afterEach(() => {
    cleanup();
    refusal = null;
    fetchMock.mockClear();
    vi.unstubAllGlobals();
  });

  const urls = () => fetchMock.mock.calls.map(([url]) => String(url));
  const stateOf = (label: string) =>
    screen.getByText(label, { selector: 'td div' }).closest('tr') as HTMLElement;

  it('lit les modules du site principal et affiche le profil DU site', async () => {
    renderWithCapabilities(<ModulesPage />, {
      permissions: MANAGE,
      sites: SITES,
      mainSiteId: 's2',
    });
    expect((await screen.findByTestId('site-profile')).textContent).toBe(
      'Dépôt — profil du site : Entrepôt',
    );
    await waitFor(() => expect(urls().some((u) => u.endsWith('/sites/s2/modules'))).toBe(true));
    // La synthèse de l'entreprise est seulement lue ; aucune écriture au niveau de l'entreprise.
    expect(
      fetchMock.mock.calls.some(
        ([u, i]) => /\/api\/v1\/modules\//.test(String(u)) && i?.method === 'PUT',
      ),
    ).toBe(false);
  });

  it('distingue activé, inactif (dépendance), désactivé, hors abonnement, à venir, non proposé', async () => {
    renderWithCapabilities(<ModulesPage />, {
      permissions: MANAGE,
      sites: SITES,
      mainSiteId: 's1',
    });
    await screen.findByTestId('module-state-notInProfile');
    for (const [label, state] of [
      ['Stock', 'active'],
      ['Point de vente', 'blocked'],
      ['Alertes', 'inactive'],
      ['Menu QR', 'notInPlan'],
      ['Tables', 'planned'],
      ['Inventaires', 'notInProfile'],
    ] as const) {
      const row = stateOf(label);
      expect(within(row).getByTestId(`module-state-${state}`)).toBeTruthy();
      const hasSwitch = within(row).queryByRole('switch') !== null;
      // Jamais d'interrupteur pour une activation impossible.
      expect(hasSwitch).toBe(['active', 'blocked', 'inactive'].includes(state));
    }
    expect(within(stateOf('Menu QR')).getByText(/offre supérieure/)).toBeTruthy();
  });

  it('module désactivé : activation par le mécanisme existant, portée dans l’URL', async () => {
    renderWithCapabilities(<ModulesPage />, {
      permissions: MANAGE,
      sites: SITES,
      mainSiteId: 's1',
    });
    await screen.findByTestId('module-state-inactive');
    fireEvent.click(within(stateOf('Alertes')).getByRole('switch'));
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
      expect(String(put?.[0])).toMatch(/\/sites\/s1\/modules\/alerts$/);
      expect(JSON.parse(String(put?.[1]?.body))).toEqual({ enabled: true });
    });
  });

  it('palier E.1 : module « Bientôt disponible » sans interrupteur, aucune écriture possible', async () => {
    renderWithCapabilities(<ModulesPage />, {
      permissions: MANAGE,
      sites: SITES,
      mainSiteId: 's1',
    });
    await screen.findByTestId('module-state-planned');
    const row = stateOf('Tables');
    expect(within(row).getByTestId('module-state-planned')).toBeTruthy();
    expect(within(row).queryByRole('switch')).toBeNull();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false);
  });

  it('palier E.1 : refus du serveur (module non implémenté) affiché, état relu', async () => {
    refusal = { status: 422, code: 'module_not_implemented' };
    const show = vi.fn();
    const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
    renderWithCapabilities(
      <ToastContext.Provider value={toast}>
        <ModulesPage />
      </ToastContext.Provider>,
      { permissions: MANAGE, sites: SITES, mainSiteId: 's1' },
    );
    await screen.findByTestId('module-state-inactive');
    fireEvent.click(within(stateOf('Alertes')).getByRole('switch'));
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({
          severity: 'error',
          summary: "Ce module n'est pas encore disponible : il ne peut pas être activé.",
        }),
      ),
    );
    // L'interface ne présume pas du succès : l'état affiché reste celui du serveur.
    expect(within(stateOf('Alertes')).getByTestId('module-state-inactive')).toBeTruthy();
  });

  it('site demandé par la page Sites (?site=)', async () => {
    renderWithCapabilities(<ModulesPage />, {
      permissions: MANAGE,
      sites: SITES,
      mainSiteId: 's1',
      path: '/organization/modules',
      route: '/organization/modules?site=s2',
    });
    expect((await screen.findByTestId('site-profile')).textContent).toMatch(/^Dépôt/);
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
    renderWithCapabilities(<ModulesPage />, { permissions: MANAGE, sites: [] });
    expect(await screen.findByText(/Créez d'abord un site/)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('profil du site inconnu : repli neutre', async () => {
    renderWithCapabilities(<ModulesPage />, {
      permissions: MANAGE,
      sites: [{ id: 's9', name: 'Annexe', code: 'ANX', kind: 'other', profile: null }],
      mainSiteId: 's9',
    });
    expect((await screen.findByTestId('site-profile')).textContent).toBe(
      'Annexe — profil du site : Profil non défini',
    );
  });
});
