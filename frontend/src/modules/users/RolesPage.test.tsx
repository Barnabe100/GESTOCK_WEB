// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities } from '@/shared/testing';

import type { Permission, Role } from './api';
import RolesPage from './RolesPage';

const PERMISSIONS: Permission[] = [
  {
    code: 'catalog.article.view',
    module: 'catalog',
    access: 'read',
    resource: 'article',
    action: 'view',
  },
  {
    code: 'catalog.article.create',
    module: 'catalog',
    access: 'write',
    resource: 'article',
    action: 'create',
  },
  { code: 'stock.entry.view', module: 'stock', access: 'read', resource: 'entry', action: 'view' },
];

const role = (overrides: Partial<Role>): Role => ({
  id: 'r',
  name: 'Rôle',
  description: null,
  template_code: null,
  is_system: false,
  is_active: true,
  protected: false,
  member_count: 0,
  permission_codes: [],
  delegable: true,
  ...overrides,
});

const ROLES: Role[] = [
  role({
    id: 'r-admin',
    name: 'Administrateur',
    template_code: 'administrator',
    is_system: true,
    protected: true,
    permission_codes: PERMISSIONS.map((p) => p.code),
  }),
  role({
    id: 'r-seller',
    name: 'Vendeur',
    template_code: 'seller',
    is_system: true,
    permission_codes: ['catalog.article.view'],
  }),
  role({
    id: 'r-cash',
    name: 'Caissier',
    member_count: 2,
    permission_codes: ['catalog.article.view'],
  }),
];

const MANAGE = ['users.role.view', 'users.role.manage', 'users.member.view'];

describe('page Rôles', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url, init) => {
      const path = String(url);
      if (path.endsWith('/role-templates')) return jsonResponse([]);
      if (path.endsWith('/permissions')) return jsonResponse(PERMISSIONS);
      // Délégation (calculée par le serveur) : ici, tout est délégable.
      if (path.includes('/permissions/delegable')) return jsonResponse(PERMISSIONS);
      if (path.endsWith('/deactivate')) {
        const body = JSON.parse(String(init?.body ?? '{}')) as { confirm?: boolean };
        return body.confirm
          ? jsonResponse({ ...ROLES[2], is_active: false })
          : jsonResponse(
              {
                code: 'role_in_use',
                detail: 'Rôle attribué',
                count: 2,
                members: [
                  { membership_id: 'm1', full_name: 'Awa', site_id: null },
                  { membership_id: 'm2', full_name: 'Moussa', site_id: null },
                ],
              },
              409,
            );
      }
      if (path.includes('/members')) return jsonResponse([]);
      return jsonResponse(ROLES);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('sépare rôles de base et rôles personnalisés ; le rôle protégé ne se désactive pas', async () => {
    renderWithCapabilities(<RolesPage />, { permissions: MANAGE });
    const system = await screen.findByRole('region', { name: 'Rôles de base' });
    const custom = screen.getByRole('region', { name: 'Rôles personnalisés' });
    expect(await within(system).findByText('Administrateur')).toBeTruthy();
    expect(within(system).getByText('Protégé')).toBeTruthy();
    expect(within(custom).getByText('Caissier')).toBeTruthy();
    expect(within(custom).queryByText('Vendeur')).toBeNull();
    // Administrateur protégé : aucune action de désactivation sur sa ligne.
    const adminRow = within(system).getByText('Administrateur').closest('tr') as HTMLElement;
    expect(within(adminRow).queryByRole('button', { name: 'Désactiver' })).toBeNull();
    const sellerRow = within(system).getByText('Vendeur').closest('tr') as HTMLElement;
    expect(within(sellerRow).getByRole('button', { name: 'Désactiver' })).toBeTruthy();
  });

  it('affiche un rôle de base en lecture seule, avec ses permissions venues de l’API', async () => {
    renderWithCapabilities(<RolesPage />, { permissions: MANAGE, isOwner: true });
    const system = await screen.findByRole('region', { name: 'Rôles de base' });
    const row = (await within(system).findByText('Vendeur')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Consulter' }));
    expect(await screen.findByText(/Rôle de base : lecture seule/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Enregistrer' })).toBeNull();
    // Par défaut, seules les permissions accordées sont listées ; toutes sont désactivées.
    const granted = await screen.findByLabelText('Consulter le catalogue');
    expect((granted as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByLabelText('Créer un article')).toBeNull();
  });

  it('recherche une permission lors de la création d’un rôle', async () => {
    renderWithCapabilities(<RolesPage />, { permissions: MANAGE, isOwner: true });
    fireEvent.click(await screen.findByRole('button', { name: 'Nouveau rôle' }));
    expect(await screen.findByLabelText('Créer un article')).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText('Rechercher une permission…'), {
      target: { value: 'entrées' },
    });
    await waitFor(() => expect(screen.queryByLabelText('Créer un article')).toBeNull());
    expect(screen.getByLabelText('Consulter les entrées de stock')).toBeTruthy();
  });

  it('demande confirmation avant de désactiver un rôle attribué', async () => {
    renderWithCapabilities(<RolesPage />, { permissions: MANAGE });
    const custom = await screen.findByRole('region', { name: 'Rôles personnalisés' });
    const row = (await within(custom).findByText('Caissier')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Désactiver' }));
    expect(await screen.findByText(/attribué à 2 membre\(s\) : Awa, Moussa/)).toBeTruthy();
    // Bouton d'acceptation de la boîte de confirmation (le dernier « Désactiver » affiché).
    fireEvent.click(screen.getAllByRole('button', { name: 'Désactiver' }).at(-1) as HTMLElement);
    await waitFor(() => {
      const bodies = fetchMock.mock.calls
        .filter(([url]) => String(url).endsWith('/roles/r-cash/deactivate'))
        .map(([, init]) => String(init?.body));
      expect(bodies).toEqual(['{"confirm":false}', '{"confirm":true}']);
    });
  });

  it("l'éditeur n'offre que les permissions délégables selon le serveur (jamais selon l'écran)", async () => {
    fetchMock.mockImplementation(async (url) => {
      const path = String(url);
      if (path.endsWith('/role-templates')) return jsonResponse([]);
      if (path.endsWith('/permissions')) return jsonResponse(PERMISSIONS);
      if (path.includes('/permissions/delegable')) return jsonResponse([PERMISSIONS[0]]);
      return jsonResponse(ROLES);
    });
    // L'utilisateur détient « Créer un article » dans le site courant : sans effet ici.
    renderWithCapabilities(<RolesPage />, {
      permissions: [...MANAGE, 'catalog.article.view', 'catalog.article.create'],
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Nouveau rôle' }));
    const create = (await screen.findByLabelText('Créer un article')) as HTMLInputElement;
    await waitFor(() => expect(create.disabled).toBe(true));
    expect((screen.getByLabelText('Consulter le catalogue') as HTMLInputElement).disabled).toBe(
      false,
    );
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).endsWith('/permissions/delegable')),
    ).toBe(true);
  });

  it('rôle hors périmètre : signalé, consultable seulement', async () => {
    fetchMock.mockImplementation(async (url) => {
      const path = String(url);
      if (path.endsWith('/role-templates')) return jsonResponse([]);
      if (path.endsWith('/permissions') || path.includes('/permissions/delegable'))
        return jsonResponse(PERMISSIONS);
      if (path.includes('/members')) return jsonResponse([]);
      return jsonResponse([...ROLES.slice(0, 2), { ...ROLES[2], delegable: false }]);
    });
    renderWithCapabilities(<RolesPage />, { permissions: MANAGE });
    const custom = await screen.findByRole('region', { name: 'Rôles personnalisés' });
    const row = (await within(custom).findByText('Caissier')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Hors de votre périmètre')).toBeTruthy();
    expect(within(row).queryByRole('button', { name: 'Désactiver' })).toBeNull();
    expect(within(row).queryByRole('button', { name: 'Dupliquer' })).toBeNull();
    fireEvent.click(within(row).getByRole('button', { name: 'Consulter' }));
    expect(await screen.findByTestId('role-out-of-scope')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Enregistrer' })).toBeNull();
  });

  it('conserve les permissions hors offre à l’enregistrement', async () => {
    const legacy = role({
      id: 'r-log',
      name: 'Logistique',
      permission_codes: ['catalog.article.view', 'stock.transfer.create'],
    });
    fetchMock.mockImplementation(async (url, init) => {
      const path = String(url);
      if (init?.method === 'PATCH') return jsonResponse(legacy);
      if (path.endsWith('/role-templates')) return jsonResponse([]);
      if (path.endsWith('/permissions') || path.includes('/permissions/delegable'))
        return jsonResponse(PERMISSIONS);
      if (path.includes('/members')) return jsonResponse([]);
      return jsonResponse([...ROLES, legacy]);
    });
    renderWithCapabilities(<RolesPage />, { permissions: MANAGE, isOwner: true });
    const custom = await screen.findByRole('region', { name: 'Rôles personnalisés' });
    const row = (await within(custom).findByText('Logistique')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Modifier' }));
    expect(await screen.findByText(/conservées sans effet/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
      expect(JSON.parse(String(patch?.[1]?.body)).permissions).toEqual([
        'catalog.article.view',
        'stock.transfer.create',
      ]);
    });
  });
});
