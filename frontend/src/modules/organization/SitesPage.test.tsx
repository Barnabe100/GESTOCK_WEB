// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
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
