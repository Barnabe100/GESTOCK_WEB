// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities } from '@/shared/testing';

import type { SiteProfilePreview } from './api';
import { SiteProfileDialog } from './SiteProfileDialog';

const SLOW = { timeout: 3000 };

const SITE = {
  id: 's1',
  name: 'Boutique',
  code: 'BTQ',
  kind: 'store' as const,
  address: null,
  phone: null,
  is_active: true,
  created_at: '2026-10-01T00:00:00Z',
  business_profile_code: 'retail.quincaillerie',
};

const CATALOG = {
  sectors: [
    { code: 'retail', name: 'Commerce', icon: null, sort_order: 1 },
    { code: 'distribution', name: 'Distribution', icon: null, sort_order: 2 },
  ],
  profiles: [
    { code: 'retail.quincaillerie', name: 'Quincaillerie', sector: 'retail' },
    { code: 'distribution.entrepot', name: 'Entrepôt', sector: 'distribution' },
  ].map((p) => ({
    ...p,
    description: null,
    ux_profile: null,
    sort_order: 1,
    is_active: true,
    default_modules: [],
    optional_modules: [],
  })),
};

const STATE = { in_profile: true, in_plan: true, activated: true, effective: true };

function preview(level: SiteProfilePreview['level'], extra: Partial<SiteProfilePreview> = {}) {
  return {
    site_id: 's1',
    site_name: 'Boutique',
    current_profile: { code: 'retail.quincaillerie', name: 'Quincaillerie', sector: 'retail' },
    target_profile: { code: 'distribution.entrepot', name: 'Entrepôt', sector: 'distribution' },
    level,
    fingerprint: 'f'.repeat(64),
    confirmation_text: level === 'STRONG' ? 'CHANGER DE PROFIL' : null,
    plan: { code: 'ENTREPRISE', compatibility: 'FULL', modules_not_in_plan: [] },
    modules: [
      {
        code: 'pos',
        status: 'available',
        change: 'removed',
        action: 'disable',
        reason: 'removed_from_profile',
        before: STATE,
        after: { ...STATE, in_profile: false, activated: false, effective: false },
      },
    ],
    summary: {
      added: [],
      removed: ['pos'],
      kept: [],
      not_in_plan: [],
      activated: [],
      deactivated: ['pos'],
    },
    history:
      level === 'SIMPLE'
        ? []
        : [
            {
              kind: 'sales_validated',
              module: 'sales',
              count: 10000,
              capped: true,
              blocking: false,
            },
          ],
    open_operations: [],
    blockers:
      level === 'BLOCKED'
        ? [
            {
              kind: 'cash_sessions_open',
              module: 'cash_register',
              count: 1,
              capped: false,
              blocking: true,
            },
          ]
        : [],
    configuration: [],
    assortment_unchanged: true,
    ...extra,
  } satisfies SiteProfilePreview;
}

const fetchMock = vi.fn();

function serve(level: SiteProfilePreview['level'], put?: () => Response) {
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') return put ? put() : jsonResponse({});
    if (url.includes('/business-profile/preview')) return jsonResponse(preview(level));
    return jsonResponse(CATALOG);
  });
}

const puts = () =>
  fetchMock.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')
    .map(([url, init]) => [
      String(url),
      JSON.parse(String((init as RequestInit).body)) as Record<string, unknown>,
    ]);

async function choose(onCreateSite = vi.fn()) {
  renderWithCapabilities(
    <SiteProfileDialog site={SITE} onClose={vi.fn()} onCreateSite={onCreateSite} />,
    { permissions: ['organization.profile.manage', 'organization.site.manage'] },
  );
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(
    document.querySelector('#site-profile-target')?.closest('.p-dropdown') as Element,
  );
  await screen.findAllByRole('option', { name: 'Entrepôt', hidden: true }, SLOW);
  const items = [...document.querySelectorAll('.p-dropdown-panel .p-dropdown-item')];
  // Le profil actuel du site n'est jamais proposé.
  expect(items.map((i) => i.textContent)).toEqual(['Entrepôt']);
  fireEvent.click(items[0] as Element);
  await within(dialog).findByTestId('profile-preview', {}, SLOW);
  return { dialog, onCreateSite };
}

describe('changement de profil d’un site (palier D)', () => {
  beforeEach(() => vi.stubGlobal('fetch', fetchMock));
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('SIMPLE : confirmation normale, aperçu et empreinte transmis au serveur', async () => {
    serve('SIMPLE');
    const { dialog } = await choose();
    expect(within(dialog).getByText('Changement simple')).toBeTruthy();
    expect(within(dialog).getByTestId('module-pos').textContent).toContain('Désactivé');
    expect(document.querySelector('#site-profile-confirmation')).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Changer de profil' }));
    await waitFor(() =>
      expect(puts()).toEqual([
        [
          '/api/v1/sites/s1/business-profile',
          { profile_code: 'distribution.entrepot', preview_fingerprint: 'f'.repeat(64) },
        ],
      ]),
    );
  });

  it('STRONG : le texte exact est exigé avant l’envoi', async () => {
    serve('STRONG');
    const { dialog } = await choose();
    expect(within(dialog).getByText('Changement important')).toBeTruthy();
    expect(within(dialog).getByText(/Ventes validées : 10000\+/)).toBeTruthy();
    const button = within(dialog).getByRole('button', { name: 'Changer de profil' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    const input = document.querySelector('#site-profile-confirmation') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'changer de profil' } });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input, { target: { value: 'CHANGER DE PROFIL' } });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() =>
      expect(puts()[0]?.[1]).toMatchObject({ confirmation: 'CHANGER DE PROFIL' }),
    );
  });

  it('BLOCKED : raisons affichées, aucune confirmation, création d’un site proposée', async () => {
    serve('BLOCKED');
    const { dialog, onCreateSite } = await choose();
    expect(within(dialog).getByText('Changement impossible')).toBeTruthy();
    expect(within(dialog).getByText(/Sessions de caisse ouvertes : 1/)).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Changer de profil' })).toBeNull();
    expect(document.querySelector('#site-profile-confirmation')).toBeNull();
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Créer un nouveau site avec ce profil' }),
    );
    expect(onCreateSite).toHaveBeenCalledWith('distribution.entrepot');
    expect(puts()).toEqual([]);
  });

  it('aperçu obsolète (409) : nouvel aperçu demandé', async () => {
    serve('SIMPLE', () => jsonResponse({ code: 'profile_preview_outdated', detail: 'x' }, 409));
    const { dialog } = await choose();
    const previews = () =>
      fetchMock.mock.calls.filter(([url]) => String(url).includes('/preview')).length;
    const before = previews();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Changer de profil' }));
    await waitFor(() => expect(previews()).toBeGreaterThan(before));
  });

  it('profil hors abonnement : avertissement explicite', async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url.includes('/preview')
        ? jsonResponse(
            preview('SIMPLE', {
              plan: {
                code: 'STANDARD',
                compatibility: 'PARTIAL',
                modules_not_in_plan: ['restaurant.qr'],
              },
            }),
          )
        : jsonResponse(CATALOG),
    );
    const { dialog } = await choose();
    expect(within(dialog).getByText(/l'abonnement ne peut pas être contourné/)).toBeTruthy();
  });
});
