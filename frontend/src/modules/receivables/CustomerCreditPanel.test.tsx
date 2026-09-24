// @vitest-environment jsdom
import { cleanup, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import CustomerDetailPage from '@/modules/customers/CustomerDetailPage';
import { formatMoney } from '@/shared/lib/decimal';
import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';

import CustomerCreditPanel from './CustomerCreditPanel';
import type { CreditExposure } from './api';
import { exposure, receivable, VIEW } from './testData';

const money = (v: string) => formatMoney(v, 'XOF', 'fr').replace(/\s/g, ' ');
const text = (el: HTMLElement) => (el.textContent ?? '').replace(/\s/g, ' ');

describe('compte client : limite, exposition, crédit disponible, créances', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const serve = (e: CreditExposure) =>
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/credit-exposure')
        ? jsonResponse(e)
        : pageOf([receivable(), receivable({ sale_id: 'v3', sale_number: 'VTE-000003' })]),
    );

  beforeEach(() => vi.stubGlobal('fetch', fetchMock));

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('limite configurée : limite, exposition, disponible et créances du client', async () => {
    serve(exposure());
    renderWithCapabilities(<CustomerCreditPanel customerId="c1" />, { permissions: VIEW });
    const credit = await screen.findByRole('group', { name: 'Crédit du client' });
    expect(text(credit)).toContain(`${money('500000.00')}Limite de crédit`);
    expect(text(credit)).toContain(`${money('320000.00')}Exposition actuelle`);
    expect(text(credit)).toContain(`${money('180000.00')}Crédit disponible`);
    expect(text(credit)).toContain('2 créances ouvertes');
    expect(await screen.findByText('VTE-000003')).toBeTruthy();
    // Liste du client : pas de colonne client répétée.
    expect(screen.queryByText('Awa Traoré')).toBeNull();
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).includes('/customers/c1/receivables?')),
    ).toBe(true);
  });

  it('limite non configurée : « Non configurée », aucun faux montant disponible', async () => {
    serve(exposure({ credit_limit: null, limit_configured: false, available_credit: null }));
    renderWithCapabilities(<CustomerCreditPanel customerId="c1" />, { permissions: VIEW });
    const credit = await screen.findByRole('group', { name: 'Crédit du client' });
    expect(text(credit)).toContain('Non configuréeLimite de crédit');
    expect(text(credit)).not.toContain('Crédit disponible');
  });

  it('dépassement de limite signalé', async () => {
    serve(exposure({ credit_limit: '300000.00', available_credit: '0.00', over_limit: true }));
    renderWithCapabilities(<CustomerCreditPanel customerId="c1" />, { permissions: VIEW });
    expect(await screen.findByText(/L'exposition dépasse la limite de crédit/)).toBeTruthy();
  });

  it('vue limitée à certains sites : pas de crédit disponible consolidé', async () => {
    serve(exposure({ consolidated: false, available_credit: null, over_limit: null }));
    renderWithCapabilities(<CustomerCreditPanel customerId="c1" />, { permissions: VIEW });
    expect(await screen.findByText(/Exposition limitée à vos sites/)).toBeTruthy();
    expect(screen.queryByText('Crédit disponible')).toBeNull();
  });
});

describe('fiche client et module Créances', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const CUSTOMER = {
    id: 'c1',
    code: 'CLI-000001',
    customer_type: 'INDIVIDUAL',
    name: 'Awa Traoré',
    legal_name: null,
    tax_id: null,
    phone: null,
    phone2: null,
    email: null,
    address: null,
    city: null,
    country: null,
    notes: null,
    credit_limit: '500000.00',
    is_active: true,
    created_at: '2026-09-24T08:00:00Z',
    updated_at: '2026-09-24T09:30:00Z',
  };

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/credit-exposure')) return jsonResponse(exposure());
      if (u.includes('/receivables')) return pageOf([receivable()]);
      return jsonResponse(CUSTOMER);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('avec la permission : compte client affiché sur la fiche', async () => {
    renderWithCapabilities(<CustomerDetailPage />, {
      permissions: ['customers.customer.view', ...VIEW],
      path: '/customers/:id',
      route: '/customers/c1',
    });
    expect(await screen.findByRole('heading', { name: 'Compte client' })).toBeTruthy();
    expect(await screen.findByText('VTE-000001')).toBeTruthy();
  });

  it('sans la permission : aucun appel ni affichage des créances', async () => {
    renderWithCapabilities(<CustomerDetailPage />, {
      permissions: ['customers.customer.view'],
      path: '/customers/:id',
      route: '/customers/c1',
    });
    expect(await screen.findByRole('heading', { name: 'Awa Traoré' })).toBeTruthy();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByText('Compte client')).toBeNull();
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('receivables'))).toBe(false);
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('credit-exposure'))).toBe(false);
  });
});
