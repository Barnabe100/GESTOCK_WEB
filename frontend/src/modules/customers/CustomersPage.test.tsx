// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';

import type { Customer } from './api';
import CustomersPage from './CustomersPage';

const customer = (overrides: Partial<Customer>): Customer => ({
  id: 'c1',
  code: 'CLI-000001',
  customer_type: 'INDIVIDUAL',
  name: 'Awa Traoré',
  legal_name: null,
  tax_id: null,
  phone: '70112233',
  phone2: null,
  email: 'awa@example.com',
  address: null,
  city: null,
  country: null,
  notes: null,
  credit_limit: null,
  is_active: true,
  created_at: '2026-09-24T08:00:00Z',
  updated_at: '2026-09-24T08:00:00Z',
  ...overrides,
});

const CUSTOMERS = [
  customer({}),
  customer({
    id: 'c2',
    code: 'CLI-000002',
    customer_type: 'BUSINESS',
    name: 'Quincaillerie du Centre',
    legal_name: 'QDC SARL',
    is_active: false,
  }),
];

const MANAGE = [
  'customers.customer.view',
  'customers.customer.create',
  'customers.customer.update',
  'customers.customer.status',
];

const listCalls = (fetchMock: ReturnType<typeof vi.fn<typeof fetch>>) =>
  fetchMock.mock.calls
    .filter(([url, init]) => String(url).includes('/customers?') && init?.method === 'GET')
    .map(([url]) => String(url));

describe('page Clients', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url, init) => {
      if (init?.method === 'POST' && String(url).endsWith('/customers')) {
        return jsonResponse(customer({ id: 'c3', code: 'CLI-000003' }), 201);
      }
      if (init?.method === 'POST') return jsonResponse({ ...CUSTOMERS[0], is_active: false });
      return pageOf(CUSTOMERS);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('affiche la liste traduite ; consultation seule sans actions de gestion', async () => {
    renderWithCapabilities(<CustomersPage />, { permissions: ['customers.customer.view'] });
    expect(await screen.findByText('Awa Traoré')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Clients' })).toBeTruthy();
    expect(screen.getByText('CLI-000002')).toBeTruthy();
    expect(screen.getByText('QDC SARL')).toBeTruthy();
    expect(screen.getByText('Entreprise')).toBeTruthy();
    expect(screen.getAllByText('Particulier').length).toBeGreaterThan(0);
    expect(screen.getByText('Inactif')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Nouveau client' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Modifier' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Désactiver' })).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Voir la fiche' })).toHaveLength(2);
  });

  it('transmet recherche, filtres, tri et pagination au serveur', async () => {
    renderWithCapabilities(<CustomersPage />, { permissions: MANAGE });
    await screen.findByText('Awa Traoré');
    expect(listCalls(fetchMock)[0]).toContain('limit=25');
    expect(listCalls(fetchMock)[0]).toContain('sort=name');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '70 11' } });
    await waitFor(() =>
      expect(listCalls(fetchMock).some((u) => u.includes('search=70+11'))).toBe(true),
    );
    // Ouverture de la liste déroulante « type » (conteneur PrimeReact).
    const typeFilter = screen
      .getAllByText('Tous les types')
      .map((el) => el.closest('.p-dropdown'))
      .find(Boolean) as HTMLElement;
    fireEvent.click(typeFilter);
    // Panneau en cours d'animation (jsdom) : options présentes mais considérées masquées.
    fireEvent.click(await screen.findByRole('option', { name: 'Entreprise', hidden: true }));
    await waitFor(() =>
      expect(listCalls(fetchMock).some((u) => u.includes('type=BUSINESS'))).toBe(true),
    );
  });

  it('valide et crée un client (normalisation laissée au serveur)', async () => {
    renderWithCapabilities(<CustomersPage />, { permissions: MANAGE });
    fireEvent.click(await screen.findByRole('button', { name: 'Nouveau client' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    expect(await within(dialog).findByText('Champ obligatoire')).toBeTruthy();

    fireEvent.change(within(dialog).getByLabelText('Nom et prénom'), {
      target: { value: 'Moussa' },
    });
    fireEvent.change(within(dialog).getByLabelText('Email'), { target: { value: 'moussa@' } });
    fireEvent.change(within(dialog).getByLabelText('Téléphone'), { target: { value: 'abc' } });
    fireEvent.change(within(dialog).getByLabelText('Plafond de crédit'), {
      target: { value: '1,234' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    expect(await within(dialog).findByText('Adresse email invalide')).toBeTruthy();
    expect(within(dialog).getByText(/Numéro invalide/)).toBeTruthy();
    expect(within(dialog).getByText(/Montant invalide/)).toBeTruthy();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);

    fireEvent.change(within(dialog).getByLabelText('Email'), {
      target: { value: 'moussa@example.com' },
    });
    fireEvent.change(within(dialog).getByLabelText('Téléphone'), {
      target: { value: '70 00 00 00' },
    });
    fireEvent.change(within(dialog).getByLabelText('Plafond de crédit'), {
      target: { value: '150 000,5' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Enregistrer' }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
      expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
        customer_type: 'INDIVIDUAL',
        name: 'Moussa',
        email: 'moussa@example.com',
        phone: '70 00 00 00',
        legal_name: '',
        credit_limit: '150000.5',
      });
    });
    // Enregistré : le formulaire se ferme.
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('affiche la raison sociale pour une entreprise et désactive un client', async () => {
    renderWithCapabilities(<CustomersPage />, { permissions: MANAGE });
    await screen.findByText('Awa Traoré');
    fireEvent.click(screen.getAllByRole('button', { name: 'Modifier' })[1] as HTMLElement);
    const dialog = await screen.findByRole('dialog');
    expect((within(dialog).getByLabelText('Raison sociale') as HTMLInputElement).value).toBe(
      'QDC SARL',
    );
    expect(within(dialog).getByLabelText('Nom usuel / enseigne')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Annuler' }));

    fireEvent.click(screen.getByRole('button', { name: 'Désactiver' }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            init?.method === 'POST' && String(url).endsWith('/customers/c1/deactivate'),
        ),
      ).toBe(true),
    );
    // Client inactif : action de réactivation proposée.
    expect(screen.getByRole('button', { name: 'Activer' })).toBeTruthy();
  });
});
