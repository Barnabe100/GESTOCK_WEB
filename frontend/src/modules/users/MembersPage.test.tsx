// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities, SITES } from '@/shared/testing';

import type { Member, Role } from './api';
import MembersPage from './MembersPage';

const ROLES: Partial<Role>[] = [
  { id: 'r-resp', name: 'Responsable boutique', is_system: false, is_active: true },
  { id: 'r-old', name: 'Ancien rôle', is_system: false, is_active: false },
];

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
  created_at: '2026-09-24T00:00:00Z',
};

describe('page Membres', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url, init) => {
      const path = String(url);
      if (init?.method === 'PATCH') return jsonResponse(MEMBER);
      if (path.endsWith('/roles')) return jsonResponse(ROLES);
      if (path.endsWith('/sites')) return jsonResponse(SITES);
      return jsonResponse([MEMBER]);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('conserve les rôles limités à un site et signale un rôle désactivé', async () => {
    renderWithCapabilities(<MembersPage />, {
      permissions: ['users.member.view', 'users.member.manage'],
    });
    expect(
      await screen.findByText('Ancien rôle (inactif), Responsable boutique (Boutique)'),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Modifier' }));
    expect(await screen.findByText('Rôles limités à un site')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
      expect(patch).toBeTruthy();
      expect(JSON.parse(String(patch?.[1]?.body))).toMatchObject({
        roles: [
          { role_id: 'r-old', site_id: null },
          { role_id: 'r-resp', site_id: 's1' },
        ],
        site_ids: ['s1'],
        all_sites: false,
      });
    });
  });
});
