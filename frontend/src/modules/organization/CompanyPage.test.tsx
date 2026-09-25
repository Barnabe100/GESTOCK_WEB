// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import CompanyPage from './CompanyPage';

const TENANT = {
  id: 't',
  name: 'Supérette Awa',
  slug: 'awa',
  business_profile_code: 'retail.alimentation',
  currency: 'XOF',
  locale: 'fr',
  timezone: 'Africa/Ouagadougou',
};

const CATALOG = {
  sectors: [
    { code: 'retail', name: 'Commerce', icon: 'pi pi-shopping-bag', sort_order: 10 },
    { code: 'distribution', name: 'Distribution', icon: 'pi pi-truck', sort_order: 40 },
  ],
  profiles: [
    { code: 'retail.alimentation', name: 'Alimentation', sector: 'retail' },
    { code: 'distribution.entrepot', name: 'Entrepôt', sector: 'distribution' },
  ].map((p) => ({
    ...p,
    description: null,
    ux_profile: `${p.sector}.default`,
    sort_order: 10,
    is_active: true,
    default_modules: [],
    optional_modules: [],
  })),
};

describe('Entreprise : profil d’activité', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('affiche secteur et profil ; sans droit de gestion, aucun changement proposé', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(TENANT));
    renderWithCapabilities(<CompanyPage />, {
      permissions: ['organization.tenant.view', 'organization.profile.view'],
    });
    expect(await screen.findByText('Profil d’activité'.replace('’', "'"))).toBeTruthy();
    expect(screen.getByTestId('company-profile').textContent).toBe('Alimentation / Supérette');
    expect(screen.getByText('Commerce de détail')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Changer de profil' })).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/business-profiles'))).toBe(
      false,
    );
  });

  it('changement confirmé ; refus du serveur expliqué avec les modules concernés', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      if (u.endsWith('/business-profiles')) return jsonResponse(CATALOG);
      if (u.endsWith('/tenant/business-profile') && init?.method === 'PUT') {
        return jsonResponse(
          {
            code: 'profile_change_incompatible',
            detail: 'incompatible',
            modules: ['cash_register', 'pos'],
          },
          409,
        );
      }
      return jsonResponse(TENANT);
    });
    const show = vi.fn();
    const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
    renderWithCapabilities(
      <ToastContext.Provider value={toast}>
        <CompanyPage />
      </ToastContext.Provider>,
      {
        permissions: [
          'organization.tenant.view',
          'organization.profile.view',
          'organization.profile.manage',
        ],
      },
    );
    const change = await screen.findByRole('button', { name: 'Changer de profil' });
    expect(change.hasAttribute('disabled')).toBe(true);

    fireEvent.click(
      document.querySelector('#new-business-profile')?.closest('.p-dropdown') as Element,
    );
    fireEvent.click(await screen.findByText('Entrepôt'));
    await waitFor(() => expect(change.hasAttribute('disabled')).toBe(false));
    fireEvent.click(change);

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Passer au profil « Entrepôt »/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Changer de profil' }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith('/tenant/business-profile') &&
            init?.method === 'PUT' &&
            init.body === JSON.stringify({ code: 'distribution.entrepot' }),
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(
        expect.objectContaining({
          severity: 'error',
          summary: expect.stringContaining('Caisse, Point de vente') as string,
        }),
      ),
    );
  });
});
