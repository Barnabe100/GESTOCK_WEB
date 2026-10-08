// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities } from '@/shared/testing';

import SitesPage from './SitesPage';

describe('sites : création directe depuis un lien (?create=1)', () => {
  beforeEach(() =>
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse([])),
    ),
  );
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const render = (permissions: string[]) =>
    renderWithCapabilities(<SitesPage />, {
      permissions,
      path: '/organization/sites',
      route: '/organization/sites?create=1',
    });

  it('ouvre le formulaire de création, champs obligatoires marqués « * »', async () => {
    render(['organization.site.view', 'organization.site.manage']);
    const dialog = await screen.findByRole('dialog');
    expect(dialog.querySelector('label[for="site-name"]')?.textContent).toContain('*');
    expect(dialog.querySelector('label[for="site-code"]')?.textContent).toContain('*');
    expect(dialog.querySelector('label[for="site-address"]')?.textContent).not.toContain('*');
    fireEvent.click(screen.getByRole('button', { name: 'Annuler' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('sans permission de gestion : aucun formulaire', async () => {
    render(['organization.site.view']);
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('sites : 1 site = 1 abonnement (ADR-0033)', () => {
  const PLANS = {
    contact_email: null,
    plans: [
      {
        code: 'STANDARD',
        name: 'Standard',
        description: null,
        contact_required: false,
        self_service: true,
        trial_days: 0,
        price_displayed: true,
        currency: 'XOF',
        periods: [{ billing_period: 'monthly', price: '10000.00' }],
        limits: {},
        modules: [],
      },
      {
        code: 'ENTREPRISE',
        name: 'Entreprise',
        description: null,
        contact_required: true,
        self_service: false,
        trial_days: 0,
        price_displayed: false,
        currency: null,
        periods: [],
        limits: {},
        modules: [],
      },
    ],
  };
  const fetchMock = vi.fn<typeof fetch>();
  const posted = () =>
    fetchMock.mock.calls
      .filter(([, init]) => init?.method === 'POST')
      .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

  function api(subscriptions: unknown[]) {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'POST') return jsonResponse({ id: 'new' }, 201);
      if (u.endsWith('/public/plans')) return jsonResponse(PLANS);
      if (u.endsWith('/subscriptions')) return jsonResponse(subscriptions);
      return jsonResponse([]);
    });
  }
  beforeEach(() => vi.stubGlobal('fetch', fetchMock));
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  const render = () =>
    renderWithCapabilities(<SitesPage />, {
      permissions: [
        'organization.site.view',
        'organization.site.manage',
        'subscription.subscription.view',
      ],
      path: '/organization/sites',
      route: '/organization/sites?create=1',
    });

  it('nouveau site : offre publiée obligatoire, période et postes demandés envoyés', async () => {
    api([{ id: 's-1', site: { id: 'site-1', name: 'Boutique', code: 'BTQ' } }]);
    render();
    const dialog = await screen.findByRole('dialog');
    const fieldset = await screen.findByTestId('site-subscription');
    expect(fieldset.textContent).toMatch(/en attente d'activation/);
    fireEvent.input(dialog.querySelector('#site-name') as Element, {
      target: { value: 'Dépôt' },
    });
    fireEvent.input(dialog.querySelector('#site-code') as Element, { target: { value: 'DEP' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer' }));
    expect(await screen.findByText('Champ obligatoire')).toBeTruthy();
    expect(posted()).toHaveLength(0);

    fireEvent.click(dialog.querySelector('#site-plan')?.closest('.p-dropdown') as Element);
    const panel = await waitFor(() => {
      const found = document.querySelector('.p-dropdown-panel');
      if (!found) throw new Error('panneau fermé');
      return found as HTMLElement;
    });
    // Seules les offres souscriptibles en ligne sont proposées.
    expect(panel.textContent).toContain('Standard');
    expect(panel.textContent).not.toContain('Entreprise');
    fireEvent.click(await within(panel).findByText('Standard'));
    fireEvent.input(dialog.querySelector('#site-activations') as Element, {
      target: { value: '3' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(posted()).toHaveLength(1));
    expect(posted()[0]).toMatchObject({
      name: 'Dépôt',
      code: 'DEP',
      plan_code: 'STANDARD',
      billing_period: 'monthly',
      requested_activations: 3,
    });
  });

  it('premier site : enregistrement possible seulement une fois l’abonnement connu (aucun clic perdu)', async () => {
    let release: (value: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (init?.method === 'POST') return jsonResponse({ id: 'new' }, 201);
      if (u.endsWith('/public/plans')) return jsonResponse(PLANS);
      if (u.endsWith('/subscriptions')) return pending;
      return jsonResponse([]);
    });
    render();
    const dialog = await screen.findByRole('dialog');
    fireEvent.input(dialog.querySelector('#site-name') as Element, {
      target: { value: 'Boutique centrale' },
    });
    fireEvent.input(dialog.querySelector('#site-code') as Element, {
      target: { value: 'CENTRE' },
    });
    // Abonnement d'inscription pas encore connu : l'enregistrement attend.
    const save = screen.getByRole('button', { name: 'Enregistrer' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    release(jsonResponse([{ id: 's-1', site: null }]));
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(posted()).toHaveLength(1));
    expect(posted()[0]).toMatchObject({ name: 'Boutique centrale', code: 'CENTRE' });
    expect(posted()[0]).not.toHaveProperty('plan_code');
  });

  it('premier site d’une inscription : abonnement déjà choisi, aucune offre demandée', async () => {
    api([{ id: 's-1', site: null }]);
    render();
    const fieldset = await screen.findByTestId('site-subscription');
    await waitFor(() => expect(fieldset.textContent).toMatch(/choisi à votre inscription/));
    expect(document.querySelector('#site-plan')).toBeNull();
  });
});

describe('sites : profil et modules de chaque site (palier E)', () => {
  const SITE_LIST = [
    {
      id: 's1',
      name: 'Boutique Ouaga',
      code: 'BTQ',
      kind: 'store',
      address: null,
      phone: null,
      is_active: true,
      created_at: '2026-10-01T00:00:00Z',
      business_profile_code: 'retail.alimentation',
    },
    {
      id: 's2',
      name: 'Dépôt Central',
      code: 'DEP',
      kind: 'warehouse',
      address: null,
      phone: null,
      is_active: true,
      created_at: '2026-10-01T00:00:00Z',
      business_profile_code: 'distribution.entrepot',
    },
  ];
  const module = (code: string, effective: boolean) => ({
    code,
    status: 'available',
    core: false,
    depends_on: [],
    in_profile: true,
    in_plan: true,
    activated_for_site: effective,
    effective,
  });
  beforeEach(() =>
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const u = String(url);
        if (u.endsWith('/sites/s1/modules'))
          return jsonResponse([
            module('stock', true),
            module('pos', true),
            module('alerts', false),
          ]);
        if (u.endsWith('/sites/s2/modules')) return jsonResponse([module('stock', true)]);
        if (u.endsWith('/sites')) return jsonResponse(SITE_LIST);
        return jsonResponse([]);
      }),
    ),
  );
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('SITE → PROFIL → MODULES → STATUT, profil présenté comme celui du site', async () => {
    renderWithCapabilities(<SitesPage />, {
      permissions: ['organization.site.view', 'organization.module.view'],
      path: '/organization/sites',
      route: '/organization/sites',
    });
    expect(await screen.findByRole('columnheader', { name: 'Profil du site' })).toBeTruthy();
    const store = (await screen.findByText('Boutique Ouaga')).closest('tr') as HTMLElement;
    const depot = screen.getByText('Dépôt Central').closest('tr') as HTMLElement;
    expect(store.textContent).toContain('Alimentation / Supérette');
    expect(depot.textContent).toContain('Entrepôt');
    await waitFor(() =>
      expect(within(store).getByTestId('site-modules-BTQ').textContent).toContain(
        '2 modules actifs',
      ),
    );
    expect(within(depot).getByTestId('site-modules-DEP').textContent).toContain('1 module actif');
    expect(within(depot).getByRole('link', { name: 'Voir les modules' }).getAttribute('href')).toBe(
      '/organization/modules?site=s2',
    );
  });

  it('sans la permission de voir les modules : aucune colonne ni requête de modules', async () => {
    renderWithCapabilities(<SitesPage />, {
      permissions: ['organization.site.view'],
      path: '/organization/sites',
      route: '/organization/sites',
    });
    await screen.findByText('Boutique Ouaga');
    expect(screen.queryByRole('columnheader', { name: 'Modules' })).toBeNull();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/modules'))).toBe(
      false,
    );
  });
});
