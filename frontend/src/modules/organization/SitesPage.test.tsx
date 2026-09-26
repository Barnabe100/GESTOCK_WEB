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

  it('premier site d’une inscription : abonnement déjà choisi, aucune offre demandée', async () => {
    api([{ id: 's-1', site: null }]);
    render();
    const fieldset = await screen.findByTestId('site-subscription');
    await waitFor(() => expect(fieldset.textContent).toMatch(/choisi à votre inscription/));
    expect(document.querySelector('#site-plan')).toBeNull();
  });
});
