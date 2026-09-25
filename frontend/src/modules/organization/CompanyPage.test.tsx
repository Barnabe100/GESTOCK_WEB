// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import CompanyPage from './CompanyPage';

const EMPTY = {
  trade_name: null,
  logo_url: null,
  email: null,
  phone: null,
  address: null,
  city: null,
  region: null,
  tax_id: null,
  trade_register: null,
  website: null,
  description: null,
};

const TENANT = {
  id: 't',
  name: 'Supérette Awa',
  slug: 'awa',
  business_profile_code: 'retail.alimentation',
  currency: 'XOF',
  locale: 'fr',
  timezone: 'Africa/Ouagadougou',
  country_code: 'BF',
  ...EMPTY,
};

const COUNTRIES = [
  {
    code: 'BF',
    name: 'Burkina Faso',
    currency: 'XOF',
    calling_code: 226,
    timezone: 'Africa/Ouagadougou',
  },
  { code: 'FR', name: 'France', currency: 'EUR', calling_code: 33, timezone: 'Europe/Paris' },
];

const IDENTITY = {
  name: 'Supérette Awa',
  trade_name: 'Chez Awa',
  logo_url: null,
  contact: [
    { kind: 'phone', value: '+22670112233' },
    { kind: 'locality', value: 'Ouagadougou, Burkina Faso' },
  ],
  identifiers: [{ kind: 'tax_id', value: '00012345A' }],
  missing_recommended: ['logo_url', 'email', 'address', 'region', 'trade_register'],
};

/** Réponses communes de la page (entreprise, référentiel des pays, identité documentaire). */
function base(url: unknown, tenant: object = TENANT) {
  const u = String(url);
  if (u.includes('/public/geo/countries')) return jsonResponse(COUNTRIES);
  if (u.endsWith('/tenant/document-identity')) return jsonResponse(IDENTITY);
  return jsonResponse(tenant);
}

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
    fetchMock.mockImplementation(async (url) => base(url));
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
      return base(url);
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

describe('Entreprise : configuration et identité documentaire', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const show = vi.fn();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  const UPDATE = [
    'organization.tenant.view',
    'organization.tenant.update',
    'organization.onboarding.view',
  ];

  function renderPage(permissions = UPDATE) {
    const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
    renderWithCapabilities(
      <ToastContext.Provider value={toast}>
        <CompanyPage />
      </ToastContext.Provider>,
      { permissions },
    );
  }

  const label = (id: string) => document.querySelector(`label[for="${id}"]`)?.textContent ?? '';
  const patches = () =>
    fetchMock.mock.calls.filter(
      ([url, init]) => String(url).endsWith('/tenant') && init?.method === 'PATCH',
    );

  it('étoile sur les seuls champs obligatoires ; sections obligatoires, recommandées, facultatives', async () => {
    fetchMock.mockImplementation(async (url) => base(url));
    renderPage();
    await screen.findByDisplayValue('Supérette Awa');
    expect(screen.getByText("Les champs marqués d'un * sont obligatoires.")).toBeTruthy();
    for (const legend of ['Informations obligatoires', 'Informations recommandées', 'Facultatif'])
      expect(screen.getByText(legend, { selector: 'legend' })).toBeTruthy();
    for (const group of ["Identité de l'entreprise", 'Coordonnées', 'Informations administratives'])
      expect(screen.getByRole('heading', { name: group })).toBeTruthy();
    for (const id of ['name', 'country_code', 'currency', 'timezone'])
      expect(label(id)).toContain('*');
    for (const id of [
      'trade_name',
      'logo_url',
      'email',
      'phone',
      'address',
      'city',
      'region',
      'tax_id',
      'trade_register',
      'website',
      'description',
    ])
      expect(label(id)).not.toContain('*');
    // Devise affichée, figée : aucune saisie possible.
    const currency = document.querySelector('#currency') as HTMLInputElement;
    expect([currency.value, currency.disabled]).toEqual(['XOF', true]);
    expect(screen.getByText(/Fixée à la création/)).toBeTruthy();
  });

  it('pays manquant (entreprise historique) : signalé, exigé, choisi dans le référentiel', async () => {
    const legacy = { ...TENANT, country_code: null };
    fetchMock.mockImplementation(async (url, init) =>
      init?.method === 'PATCH'
        ? jsonResponse({ ...legacy, country_code: 'FR' })
        : base(url, legacy),
    );
    renderPage();
    expect(await screen.findByTestId('country-missing')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer' }));
    expect(await screen.findByText('Champ obligatoire')).toBeTruthy();
    expect(screen.queryByText(/Too small|expected string/)).toBeNull();
    expect(patches()).toHaveLength(0);

    fireEvent.click(document.querySelector('#country_code')?.closest('.p-dropdown') as Element);
    fireEvent.click(await screen.findByRole('option', { name: /France/, hidden: true }));
    // Devise proposée par le pays, mais celle de l'entreprise reste figée.
    expect(
      await screen.findByText(
        "Devise habituelle de ce pays : EUR. La devise de l'entreprise reste XOF.",
      ),
    ).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Nom commercial'), { target: { value: ' Chez Awa ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    const body = JSON.parse(String(patches()[0]?.[1]?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      name: 'Supérette Awa',
      country_code: 'FR',
      trade_name: 'Chez Awa',
      email: null,
      tax_id: null,
    });
    expect(body).not.toHaveProperty('currency');
    await waitFor(() =>
      expect(show).toHaveBeenCalledWith(expect.objectContaining({ severity: 'success' })),
    );
  });

  it('refus du serveur rattaché au champ concerné', async () => {
    fetchMock.mockImplementation(async (url, init) =>
      init?.method === 'PATCH'
        ? jsonResponse(
            {
              code: 'validation_error',
              detail: 'Données invalides',
              errors: [{ loc: ['body', 'logo_url'], msg: 'x', type: 'value_error' }],
            },
            422,
          )
        : base(url),
    );
    renderPage();
    await screen.findByDisplayValue('Supérette Awa');
    fireEvent.change(screen.getByLabelText(/^Logo/), {
      target: { value: 'https://cdn.example.com/logo.png' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    await waitFor(() => expect(show).toHaveBeenCalled());
    expect(await screen.findByText('Valeur refusée : vérifiez le format.')).toBeTruthy();
    expect(show).toHaveBeenCalledWith(expect.objectContaining({ severity: 'error' }));
  });

  it('aperçu documentaire : données enregistrées seulement, jamais « N/A »', async () => {
    fetchMock.mockImplementation(async (url) => base(url));
    renderPage();
    const preview = await screen.findByTestId('identity-preview');
    expect(preview.textContent).toContain('Supérette Awa');
    expect(preview.textContent).toContain('Chez Awa');
    expect(within(preview).getByText('Tél. : +22670112233')).toBeTruthy();
    expect(within(preview).getByText('Ouagadougou, Burkina Faso')).toBeTruthy();
    expect(within(preview).getByText('IFU : 00012345A')).toBeTruthy();
    expect(preview.textContent).not.toMatch(/N\/A|RCCM|@/);
    expect(preview.querySelector('img')).toBeNull();
    expect(screen.getByTestId('identity-completeness').textContent).toBe(
      'Informations recommandées renseignées : 4/9',
    );
  });

  it("étapes d'installation liées à la page, avec leur statut", async () => {
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/onboarding')
        ? jsonResponse({
            steps: [
              { code: 'company', status: 'COMPLETED', title: 'onboarding.steps.company.title' },
              {
                code: 'configuration',
                status: 'IN_PROGRESS',
                title: 'onboarding.steps.configuration.title',
              },
              { code: 'first_site', status: 'NOT_STARTED', title: 'x' },
            ].map((s) => ({
              ...s,
              action: {
                route:
                  s.code === 'first_site'
                    ? '/organization/sites?create=1'
                    : '/organization/company',
              },
            })),
          })
        : base(url),
    );
    renderPage();
    const steps = within(await screen.findByTestId('company-steps'));
    expect(steps.getAllByRole('listitem')).toHaveLength(2);
    expect(steps.getByText('Votre entreprise').nextSibling?.textContent).toBe('Terminée');
    expect(steps.getByText('Finaliser la configuration').nextSibling?.textContent).toBe('En cours');
  });

  it('sans droit de modification : lecture seule, ni étoile ni enregistrement', async () => {
    fetchMock.mockImplementation(async (url) => base(url));
    renderPage(['organization.tenant.view']);
    await screen.findByDisplayValue('Supérette Awa');
    expect((document.querySelector('#name') as HTMLInputElement).disabled).toBe(true);
    expect(label('name')).not.toContain('*');
    expect(screen.queryByRole('button', { name: 'Enregistrer' })).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/onboarding'))).toBe(false);
  });
});
