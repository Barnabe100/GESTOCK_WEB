// @vitest-environment jsdom
import { cleanup, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities } from '@/shared/testing';

import CustomerDetailPage from './CustomerDetailPage';

const CUSTOMER = {
  id: 'c2',
  code: 'CLI-000002',
  customer_type: 'BUSINESS',
  name: 'Quincaillerie du Centre',
  legal_name: 'QDC SARL',
  tax_id: '00012345A',
  phone: '25300000',
  phone2: null,
  email: null,
  address: null,
  city: 'Ouagadougou',
  country: null,
  notes: 'Livraison le matin',
  credit_limit: '150000.00',
  is_active: false,
  created_at: '2026-09-24T08:00:00Z',
  updated_at: '2026-09-24T09:30:00Z',
};

describe('fiche client', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async () => jsonResponse(CUSTOMER));
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('affiche les informations réelles, sans statistique fictive', async () => {
    renderWithCapabilities(<CustomerDetailPage />, {
      permissions: ['customers.customer.view'],
      path: '/customers/:id',
      route: '/customers/c2',
    });
    expect(await screen.findByRole('heading', { name: 'Quincaillerie du Centre' })).toBeTruthy();
    expect(screen.getByText('CLI-000002')).toBeTruthy();
    expect(screen.getByText('Entreprise')).toBeTruthy();
    expect(screen.getByText('QDC SARL')).toBeTruthy();
    expect(screen.getByText(/150\s000\sF\s?CFA/)).toBeTruthy();
    expect(screen.getByText('Livraison le matin')).toBeTruthy();
    expect(screen.getByText(/Client désactivé : consultable/)).toBeTruthy();
    // Champs vides non affichés ; aucune donnée commerciale inventée.
    expect(screen.queryByText('Email')).toBeNull();
    expect(screen.queryByText(/achats|montant dû|dernière vente/i)).toBeNull();
    // Consultation seule : ni modification ni réactivation.
    expect(screen.queryByRole('button', { name: 'Modifier' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Activer' })).toBeNull();
  });

  it('propose la réactivation avec la permission', async () => {
    renderWithCapabilities(<CustomerDetailPage />, {
      permissions: ['customers.customer.view', 'customers.customer.status'],
      path: '/customers/:id',
      route: '/customers/c2',
    });
    expect(await screen.findByRole('button', { name: 'Activer' })).toBeTruthy();
  });
});
