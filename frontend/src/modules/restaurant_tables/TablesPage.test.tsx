// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FRONTEND_MODULES } from '@/app/modules';
import { buildNavigationSections, buildRoutes } from '@/core/modules/registry';
import { SITES, jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import TablesPage from './TablesPage';

/** Palier R5-C1 (ADR-0049) : configuration des zones et des tables d'un site. */

const zone = (over: Record<string, unknown> = {}) => ({
  id: 'z1',
  site_id: 's1',
  site_name: 'Boutique',
  name: 'Salle',
  sort_order: 1,
  is_active: true,
  table_count: 3,
  active_table_count: 2,
  created_at: '2026-10-20T08:00:00Z',
  updated_at: '2026-10-20T08:00:00Z',
  ...over,
});

const table = (over: Record<string, unknown> = {}) => ({
  id: 't1',
  site_id: 's1',
  site_name: 'Boutique',
  zone_id: 'z1',
  zone_name: 'Salle',
  zone_active: true,
  label: 'T1',
  seats: 4,
  sort_order: 1,
  is_active: true,
  created_at: '2026-10-20T08:00:00Z',
  updated_at: '2026-10-20T08:00:00Z',
  ...over,
});

const VIEW = 'restaurant.tables.table.view';
const MANAGE = [VIEW, 'restaurant.tables.table.manage'];
const ONE_SITE = [SITES[0]!];

describe('zones et tables (palier R5-C1)', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const show = vi.fn();
  const calls = (method = 'GET') =>
    fetchMock.mock.calls
      .filter(([, init]) => (init?.method ?? 'GET') === method)
      .map(([u, init]) => [String(u), init?.body ? JSON.parse(String(init.body)) : null]);
  const gets = (fragment: string) =>
    calls()
      .map(([u]) => String(u))
      .filter((u) => u.includes(fragment));

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  const tables = () => [
    table(),
    table({ id: 't2', label: 'T2', seats: null, sort_order: 2, is_active: false }),
    table({
      id: 't3',
      label: 'Terrasse 4',
      zone_id: 'z2',
      zone_name: 'Terrasse',
      zone_active: false,
    }),
    table({ id: 't4', label: 'Bar', zone_id: null, zone_name: null, zone_active: null }),
  ];
  const zones = () => [
    zone(),
    zone({ id: 'z2', name: 'Terrasse', sort_order: 0, is_active: false, active_table_count: 0 }),
  ];

  /** API simulée : listes, puis écritures (réponse ou refus du serveur). */
  const serve = (write: (url: string, init: RequestInit) => Response | Promise<Response>) =>
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if ((init?.method ?? 'GET') !== 'GET') return write(u, init as RequestInit);
      // Plan de salle (onglet par défaut, C-2) : couvert par FloorPlan.test.tsx.
      if (u.includes('/restaurant/tables/board')) {
        return jsonResponse({
          site_id: 's1',
          site_name: 'Boutique',
          orders_visible: true,
          counts: { free: 0, occupied: 0, to_settle: 0 },
          tables: [],
        });
      }
      if (u.includes('/restaurant/tables/zones')) {
        // Liste des zones actives (choix d'une zone) : seulement les actives.
        return pageOf(u.includes('status=active') ? [zone()] : zones());
      }
      return pageOf(tables());
    });
  const ok = () => serve(() => jsonResponse(table()));

  const render = (permissions: string[], options: { sites?: typeof SITES } = {}) => {
    const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
    const wrap = (node: ReactNode) => (
      <ToastContext.Provider value={toast}>{node}</ToastContext.Provider>
    );
    const view = renderWithCapabilities(wrap(<TablesPage />), { permissions, ...options });
    // Le plan de salle est l'onglet par défaut (C-2) : ces tests portent sur la configuration.
    fireEvent.click(screen.getByRole('tab', { name: 'Tables' }));
    return view;
  };
  const row = (text: string) => screen.getByText(text).closest('tr') as HTMLElement;
  const pick = (container: HTMLElement, label: RegExp, option: string) => {
    const input = within(container).getByLabelText(label, {
      selector: 'input, select, span, div',
    });
    fireEvent.click(input.closest('.p-dropdown') ?? input);
    fireEvent.click(screen.getAllByRole('option', { name: option, hidden: true }).at(-1)!);
  };
  const zonesTab = async () => {
    fireEvent.click(screen.getByRole('tab', { name: 'Zones' }));
    return screen.findByText('Terrasse', { selector: 'td' });
  };

  it('listes des tables et des zones : zone facultative, places, zone désactivée, statuts', async () => {
    ok();
    render(MANAGE);
    expect(await screen.findByText('T1')).toBeTruthy();
    expect(within(row('T1')).getByText('Salle')).toBeTruthy();
    expect(within(row('T1')).getByText('Actif')).toBeTruthy();
    expect(within(row('T2')).getByText('Inactif')).toBeTruthy();
    expect(within(row('T2')).getByText('—')).toBeTruthy();
    expect(within(row('Terrasse 4')).getByText('Zone désactivée')).toBeTruthy();
    expect(within(row('Bar')).getByText('Sans zone')).toBeTruthy();
    // Deux sites accessibles, aucun sélectionné : la colonne Site est affichée.
    expect(screen.getByRole('columnheader', { name: 'Site' })).toBeTruthy();
    await zonesTab();
    expect(within(row('Salle')).getByText('2 / 3')).toBeTruthy();
    expect(within(row('Terrasse')).getByText('Inactif')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Nouvelle table' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Nouvelle zone' })).toBeTruthy();
  });

  it('recherche et filtres serveur : texte, état, zone et « Sans zone »', async () => {
    ok();
    render(MANAGE);
    await screen.findByText('T1');
    fireEvent.change(screen.getByPlaceholderText('Rechercher une table…'), {
      target: { value: 'ter' },
    });
    await waitFor(() =>
      expect(gets('/restaurant/tables?').some((u) => u.includes('search=ter'))).toBe(true),
    );
    const filters = screen.getByRole('search');
    pick(filters, /Zone/, 'Sans zone');
    await waitFor(() =>
      expect(gets('/restaurant/tables?').some((u) => u.includes('without_zone=true'))).toBe(true),
    );
    expect(gets('/restaurant/tables?').some((u) => u.includes('zone_id='))).toBe(false);
    // Plusieurs sites accessibles : la zone est suivie de son site.
    pick(filters, /Zone/, 'Salle (Boutique)');
    await waitFor(() => expect(gets('/restaurant/tables?').at(-1)).toMatch(/zone_id=z1/));
    expect(gets('/restaurant/tables?').at(-1)).not.toMatch(/without_zone/);
    pick(filters, /Statut/, 'Inactifs');
    await waitFor(() => expect(gets('/restaurant/tables?').at(-1)).toMatch(/status=inactive/));
    // Recherche des zones.
    await zonesTab();
    fireEvent.change(screen.getByPlaceholderText('Rechercher une zone…'), {
      target: { value: 'sal' },
    });
    await waitFor(() =>
      expect(gets('/restaurant/tables/zones?').some((u) => u.includes('search=sal'))).toBe(true),
    );
  });

  it('création d’une table : site, zone active, places et ordre envoyés au serveur', async () => {
    ok();
    render(MANAGE, { sites: ONE_SITE });
    await screen.findByText('T1');
    fireEvent.click(screen.getByRole('button', { name: 'Nouvelle table' }));
    const dialog = await screen.findByRole('dialog');
    // Libellé obligatoire : rien n'est envoyé sans lui.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    expect(await within(dialog).findByText('Champ obligatoire')).toBeTruthy();
    expect(calls('POST')).toEqual([]);
    fireEvent.change(within(dialog).getByLabelText(/Libellé/), { target: { value: ' T9 ' } });
    await waitFor(() => expect(gets('status=active').length).toBeGreaterThan(0));
    pick(dialog, /^Zone/, 'Salle');
    const seats = within(dialog).getByLabelText(/Places/);
    fireEvent.change(seats, { target: { value: '6' } });
    fireEvent.blur(seats);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('POST')).toEqual([
        [
          '/api/v1/restaurant/tables',
          { site_id: 's1', zone_id: 'z1', label: 'T9', seats: 6, sort_order: 0 },
        ],
      ]),
    );
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({ severity: 'success', summary: 'Table enregistrée' }),
      ),
    );
  });

  it('modification d’une table : PUT avec le corps COMPLET, zone désactivée conservée', async () => {
    ok();
    render(MANAGE);
    await screen.findByText('Terrasse 4');
    fireEvent.click(within(row('Terrasse 4')).getByRole('button', { name: 'Modifier' }));
    const dialog = await screen.findByRole('dialog');
    // Zone actuelle désactivée : proposée (le serveur accepte de la conserver).
    expect((within(dialog).getByLabelText(/^Zone/) as HTMLInputElement).value).toBe(
      'Terrasse (zone désactivée)',
    );
    fireEvent.change(within(dialog).getByLabelText(/Libellé/), {
      target: { value: 'Terrasse 5' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        [
          '/api/v1/restaurant/tables/t3',
          { zone_id: 'z2', label: 'Terrasse 5', seats: 4, sort_order: 1 },
        ],
      ]),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // Retirer la zone : `zone_id` envoyé à null, les autres champs inchangés.
    fireEvent.click(within(row('T1')).getByRole('button', { name: 'Modifier' }));
    const second = await screen.findByRole('dialog');
    // Icône « effacer » de PrimeReact : déclenchée au relâchement du pointeur.
    fireEvent.pointerUp(second.querySelector('.p-dropdown-clear-icon') as Element);
    fireEvent.click(within(second).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('PUT').at(-1)).toEqual([
        '/api/v1/restaurant/tables/t1',
        { zone_id: null, label: 'T1', seats: 4, sort_order: 1 },
      ]),
    );
  });

  it('création et modification d’une zone', async () => {
    ok();
    render(MANAGE, { sites: ONE_SITE });
    await zonesTab();
    fireEvent.click(screen.getByRole('button', { name: 'Nouvelle zone' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/Nom de la zone/), {
      target: { value: 'Mezzanine' },
    });
    const order = within(dialog).getByLabelText(/Ordre d'affichage/);
    fireEvent.change(order, { target: { value: '3' } });
    fireEvent.blur(order);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('POST')).toEqual([
        ['/api/v1/restaurant/tables/zones', { site_id: 's1', name: 'Mezzanine', sort_order: 3 }],
      ]),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(within(row('Salle')).getByRole('button', { name: 'Modifier' }));
    const edit = await screen.findByRole('dialog');
    fireEvent.change(within(edit).getByLabelText(/Nom de la zone/), {
      target: { value: 'Grande salle' },
    });
    fireEvent.click(within(edit).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() =>
      expect(calls('PUT')).toEqual([
        ['/api/v1/restaurant/tables/zones/z1', { name: 'Grande salle', sort_order: 1 }],
      ]),
    );
  });

  it('tables : désactivation confirmée, réactivation directe', async () => {
    ok();
    render(MANAGE);
    await screen.findByText('T1');
    fireEvent.click(within(row('T1')).getByRole('button', { name: 'Désactiver' }));
    const confirm = await screen.findByRole('dialog');
    expect(within(confirm).getByText(/commande en cours/)).toBeTruthy();
    expect(calls('POST')).toEqual([]);
    fireEvent.click(within(confirm).getByRole('button', { name: 'Désactiver' }));
    await waitFor(() =>
      expect(calls('POST').map(([u]) => u)).toEqual(['/api/v1/restaurant/tables/t1/deactivate']),
    );
    fireEvent.click(within(row('T2')).getByRole('button', { name: 'Activer' }));
    await waitFor(() =>
      expect(calls('POST').map(([u]) => u)).toContain('/api/v1/restaurant/tables/t2/activate'),
    );
  });

  it('zones : désactivation confirmée, réactivation directe', async () => {
    ok();
    render(MANAGE);
    await zonesTab();
    fireEvent.click(within(row('Salle')).getByRole('button', { name: 'Désactiver' }));
    const confirm = await screen.findByRole('dialog');
    expect(within(confirm).getByText(/tables actives/)).toBeTruthy();
    fireEvent.click(within(confirm).getByRole('button', { name: 'Désactiver' }));
    await waitFor(() =>
      expect(calls('POST').map(([u]) => u)).toEqual([
        '/api/v1/restaurant/tables/zones/z1/deactivate',
      ]),
    );
    fireEvent.click(within(row('Terrasse')).getByRole('button', { name: 'Activer' }));
    await waitFor(() =>
      expect(calls('POST').map(([u]) => u)).toContain(
        '/api/v1/restaurant/tables/zones/z2/activate',
      ),
    );
  });

  it('refus métier du serveur traduits', async () => {
    const refusals: Record<string, [number, string]> = {
      '/restaurant/tables/t1/deactivate': [409, 'table_has_open_orders'],
      '/restaurant/tables/t2/activate': [422, 'zone_inactive'],
      '/restaurant/tables/zones/z1/deactivate': [409, 'zone_has_active_tables'],
    };
    serve((url) => {
      const path = Object.keys(refusals).find((p) => url.endsWith(p));
      if (path) {
        const [status, code] = refusals[path]!;
        return jsonResponse({ status, title: 'Refus', code }, status);
      }
      return jsonResponse({ status: 409, title: 'Conflit', code: 'table_label_taken' }, 409);
    });
    render(MANAGE);
    await screen.findByText('T1');
    const error = (summary: string) =>
      waitFor(() =>
        expect(show).toHaveBeenCalledWith(expect.objectContaining({ severity: 'error', summary })),
      );
    fireEvent.click(within(row('T1')).getByRole('button', { name: 'Désactiver' }));
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Désactiver' }),
    );
    await error(
      'Des commandes en cours sont rattachées à cette table : terminez-les ou changez-les de table avant de la désactiver.',
    );
    fireEvent.click(within(row('T2')).getByRole('button', { name: 'Activer' }));
    await error("Cette zone est désactivée : choisissez une zone active ou réactivez-la d'abord.");
    fireEvent.click(within(row('T1')).getByRole('button', { name: 'Modifier' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await error('Une table de ce libellé existe déjà sur ce site.');
    // Le dialogue reste ouvert après un refus.
    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Annuler' }));
    await zonesTab();
    fireEvent.click(within(row('Salle')).getByRole('button', { name: 'Désactiver' }));
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Désactiver' }),
    );
    await error(
      "Cette zone contient encore des tables actives : désactivez-les ou changez-les de zone d'abord.",
    );
  });

  it('les dix codes d’erreur des tables et des zones sont traduits', async () => {
    const { default: errors } = await import('@/core/i18n/locales/fr/errors.json');
    const codes = [
      'zone_not_found',
      'table_not_found',
      'zone_name_taken',
      'table_label_taken',
      'zone_has_active_tables',
      'table_has_open_orders',
      'zone_inactive',
      'table_not_allowed_for_mode',
      'tables_unavailable',
      'table_inactive',
    ];
    const messages = errors as Record<string, string>;
    for (const code of codes) expect(messages[code], code).toMatch(/\S/);
  });

  it('lecture seule sans restaurant.tables.table.manage', async () => {
    ok();
    render([VIEW]);
    await screen.findByText('T1');
    expect(screen.queryByRole('button', { name: 'Nouvelle table' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Nouvelle zone' })).toBeNull();
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Modifier' })).toBeNull();
    await zonesTab();
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Désactiver' })).toBeNull();
    expect(calls('POST')).toEqual([]);
  });

  it('absent du menu et des routes tant que le backend déclare le module planifié', () => {
    const caps = (status: string) => ({
      modules: [{ code: 'restaurant.tables', status }],
      permissions: [VIEW],
      navigation: ['restaurant.tables'],
    });
    const keys = (status: string) =>
      buildNavigationSections(FRONTEND_MODULES, caps(status)).flatMap((s) =>
        s.items.map((i) => i.key),
      );
    const paths = (status: string) =>
      buildRoutes(FRONTEND_MODULES, caps(status)).map((r) => r.path);
    expect(keys('planned')).not.toContain('restaurantTables');
    expect(paths('planned')).not.toContain('restaurant/tables');
    expect(keys('available')).toContain('restaurantTables');
    expect(paths('available')).toContain('restaurant/tables');
    // Disponible mais sans la permission de consultation : ni entrée ni route.
    const noView = { ...caps('available'), permissions: [] };
    expect(buildRoutes(FRONTEND_MODULES, noView).map((r) => r.path)).not.toContain(
      'restaurant/tables',
    );
  });
});
