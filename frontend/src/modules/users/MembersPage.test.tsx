// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities, SITES } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import type { Member, Role } from './api';
import MembersPage from './MembersPage';

const ROLES: Partial<Role>[] = [
  { id: 'r-resp', name: 'Responsable boutique', is_system: false, is_active: true },
  { id: 'r-old', name: 'Ancien rôle', is_system: false, is_active: false },
  { id: 'r-dir', name: 'Directeur', is_system: false, is_active: true },
];
// Délégation calculée par le serveur : « Directeur » n'est pas attribuable par l'utilisateur.
const DELEGABLE = [ROLES[0]];

const MEMBER: Member = {
  id: 'm1',
  user_id: 'u1',
  email: 'resp@example.com',
  full_name: 'Awa Traoré',
  status: 'active',
  is_owner: false,
  all_sites: false,
  must_change_password: false,
  roles: [
    { role_id: 'r-old', site_id: null },
    { role_id: 'r-resp', site_id: 's1' },
  ],
  site_ids: ['s1'],
  created_at: '2026-09-24T10:00:00Z',
};
const OWNER: Member = {
  ...MEMBER,
  id: 'm0',
  user_id: 'u-me',
  email: 'me@example.com',
  full_name: 'Moi',
  is_owner: true,
  all_sites: true,
  roles: [],
  site_ids: [],
};
const INACTIVE: Member = {
  ...MEMBER,
  id: 'm2',
  user_id: 'u2',
  email: 'paul@example.com',
  full_name: 'Paul',
  status: 'suspended',
};

const page = (items: Member[]) => ({ items, total: items.length, limit: 25, offset: 0 });
const MANAGE = ['users.member.view', 'users.member.manage'];

describe('page Utilisateurs', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const show = vi.fn();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url, init) => {
      const path = String(url);
      if (init?.method === 'PATCH' || init?.method === 'POST') return jsonResponse(MEMBER);
      if (path.includes('/roles/delegable')) return jsonResponse(DELEGABLE);
      if (path.endsWith('/roles')) return jsonResponse(ROLES);
      if (path.endsWith('/sites')) return jsonResponse(SITES);
      return jsonResponse(page([OWNER, MEMBER, INACTIVE]));
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  function render(permissions = MANAGE) {
    const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
    renderWithCapabilities(
      <ToastContext.Provider value={toast}>
        <MembersPage />
      </ToastContext.Provider>,
      { permissions },
    );
  }
  const calls = (method: string) =>
    fetchMock.mock.calls.filter(([, init]) => (init?.method ?? 'GET') === method);
  const lastList = () =>
    String(
      calls('GET')
        .map(([url]) => String(url))
        .filter((u) => u.includes('/members?'))
        .at(-1),
    );
  const row = (name: string) => screen.getByText(name).closest('tr') as HTMLElement;

  it('liste : rôles, sites, statut, date d’ajout ; aucune action sur le propriétaire ni sur soi', async () => {
    render();
    expect(
      await screen.findAllByText('Ancien rôle (inactif), Responsable boutique (Boutique)'),
    ).toHaveLength(2);
    expect(within(row('Paul')).getByText('Inactif')).toBeTruthy();
    expect(within(row('Awa Traoré')).getByText('24 sept. 2026')).toBeTruthy();
    expect(within(row('Moi')).queryAllByRole('button')).toHaveLength(0);
    expect(
      within(row('Awa Traoré')).getByRole('button', { name: "Modifier l'accès" }),
    ).toBeTruthy();
    // Jamais d'action sur l'identité globale ni de suppression.
    expect(screen.queryByRole('button', { name: /mot de passe|supprimer/i })).toBeNull();
  });

  it('recherche et filtres envoyés au serveur (pagination côté serveur)', async () => {
    render();
    await screen.findByText('Paul');
    expect(lastList()).toContain('sort=full_name');
    fireEvent.change(screen.getByPlaceholderText('Rechercher un nom ou un e-mail'), {
      target: { value: 'awa' },
    });
    await waitFor(() => expect(lastList()).toContain('search=awa'));
    fireEvent.click(
      screen.getByLabelText('Statut', { selector: '[aria-label]' }).closest('.p-dropdown')!,
    );
    fireEvent.click(await screen.findByRole('option', { name: 'Inactifs', hidden: true }));
    await waitFor(() => expect(lastList()).toContain('status=inactive'));
    fireEvent.click(
      screen.getByLabelText('Rôles', { selector: 'input,[aria-label]' }).closest('.p-dropdown')!,
    );
    fireEvent.click(
      await screen.findByRole('option', { name: /Responsable boutique/, hidden: true }),
    );
    await waitFor(() => expect(lastList()).toContain('role_id=r-resp'));
  });

  it("modifier l'accès : identité en lecture seule, aucun mot de passe, rôles et sites conservés", async () => {
    render();
    await screen.findByText('Paul');
    fireEvent.click(within(row('Awa Traoré')).getByRole('button', { name: "Modifier l'accès" }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByTestId('member-identity-name').textContent).toBe('Awa Traoré');
    expect(within(dialog).getByTestId('member-identity-email').textContent).toBe(
      'resp@example.com',
    );
    expect(within(dialog).getByText(/compte global de l'utilisateur/)).toBeTruthy();
    expect(dialog.querySelector('#member-email, #member-name, #member-password')).toBeNull();
    expect(dialog.querySelector('input[type="password"]')).toBeNull();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(calls('PATCH')).toHaveLength(1));
    const body = JSON.parse(String(calls('PATCH')[0]?.[1]?.body)) as Record<string, unknown>;
    expect(body).toEqual({
      roles: [
        { role_id: 'r-old', site_id: null },
        { role_id: 'r-resp', site_id: 's1' },
      ],
      site_ids: ['s1'],
      all_sites: false,
      status: 'active',
    });
  });

  it('ajout : e-mail et nom obligatoires, mot de passe provisoire facultatif', async () => {
    render();
    await screen.findByText('Paul');
    fireEvent.click(screen.getByRole('button', { name: 'Nouvel utilisateur' }));
    const dialog = await screen.findByRole('dialog');
    const label = (id: string) => dialog.querySelector(`label[for="${id}"]`)?.textContent ?? '';
    expect(label('member-email')).toContain('*');
    expect(label('member-name')).toContain('*');
    expect(label('member-password')).not.toContain('*');
    expect(within(dialog).getByText(/il est réutilisé tel quel/)).toBeTruthy();
    fireEvent.change(dialog.querySelector('#member-email') as Element, {
      target: { value: 'jean@example.com' },
    });
    fireEvent.change(dialog.querySelector('#member-name') as Element, {
      target: { value: 'Jean' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(calls('POST')).toHaveLength(1));
    expect(JSON.parse(String(calls('POST')[0]?.[1]?.body))).toMatchObject({
      email: 'jean@example.com',
      full_name: 'Jean',
      roles: [],
      all_sites: false,
    });
  });

  it('désactivation confirmée (ce tenant seulement) ; réactivation directe', async () => {
    render();
    await screen.findByText('Paul');
    fireEvent.click(within(row('Awa Traoré')).getByRole('button', { name: 'Désactiver' }));
    const confirm = await screen.findByRole('dialog');
    expect(within(confirm).getByText(/ses éventuelles autres entreprises/)).toBeTruthy();
    fireEvent.click(within(confirm).getByRole('button', { name: 'Désactiver' }));
    await waitFor(() =>
      expect(calls('POST').map(([u]) => String(u))).toContain('/api/v1/members/m1/deactivate'),
    );
    fireEvent.click(within(row('Paul')).getByRole('button', { name: 'Activer' }));
    await waitFor(() =>
      expect(calls('POST').map(([u]) => String(u))).toContain('/api/v1/members/m2/activate'),
    );
  });

  it('refus du serveur (anti-escalade) expliqué', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const path = String(url);
      if (init?.method === 'PATCH')
        return jsonResponse({ code: 'permission_escalation', detail: 'x' }, 403);
      if (path.includes('/roles/delegable')) return jsonResponse(DELEGABLE);
      if (path.endsWith('/roles')) return jsonResponse(ROLES);
      if (path.endsWith('/sites')) return jsonResponse(SITES);
      return jsonResponse(page([MEMBER]));
    });
    render();
    fireEvent.click(await screen.findByRole('button', { name: "Modifier l'accès" }));
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Enregistrer' }),
    );
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({
          severity: 'error',
          summary: 'Vous ne pouvez pas accorder des permissions que vous ne détenez pas.',
        }),
      ),
    );
  });

  it('consultation seule : ni ajout ni actions', async () => {
    render(['users.member.view']);
    await screen.findByText('Paul');
    expect(screen.queryByRole('button', { name: 'Nouvel utilisateur' })).toBeNull();
    expect(screen.queryByRole('button', { name: "Modifier l'accès" })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Désactiver' })).toBeNull();
  });

  it('seul utilisateur : invitation à ajouter son équipe', async () => {
    fetchMock.mockImplementation(async (url) => {
      const path = String(url);
      if (path.includes('/roles/delegable')) return jsonResponse(DELEGABLE);
      if (path.endsWith('/roles')) return jsonResponse(ROLES);
      if (path.endsWith('/sites')) return jsonResponse(SITES);
      return jsonResponse(page([OWNER]));
    });
    render();
    expect((await screen.findByTestId('members-alone')).textContent).toContain(
      'Vous êtes le seul utilisateur',
    );
  });

  it('attribution : seuls les rôles délégables (selon le serveur) sont proposés', async () => {
    render();
    await screen.findByText('Paul');
    fireEvent.click(screen.getByRole('button', { name: 'Nouvel utilisateur' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(dialog.querySelector('#member-roles')?.closest('.p-multiselect') as Element);
    const director = await screen.findByRole('option', {
      name: /Directeur \(hors de votre périmètre\)/,
      hidden: true,
    });
    expect(director.className).toContain('p-disabled');
    const resp = screen.getByRole('option', { name: /^Responsable boutique/, hidden: true });
    expect(resp.className).not.toContain('p-disabled');
  });
});
