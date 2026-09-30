// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities, SITES } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import PaymentMethodsPage from './PaymentMethodsPage';
import { PAYMENT_METHODS_FIXTURE, paymentMethodFixture } from './testData';

function withToast(element: ReactNode, show: ReturnType<typeof vi.fn>) {
  const ref = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const fetchMock = vi.fn<typeof fetch>();
const show = vi.fn();
const writes = (method: string) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === method);
const METHODS = [
  ...PAYMENT_METHODS_FIXTURE,
  paymentMethodFixture({
    id: 'pm4',
    label: 'Wave',
    kind: 'MOBILE_MONEY',
    disabled_site_ids: ['s2'],
    sort_order: 40,
  }),
];

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockImplementation(async (_url, init) =>
    init?.method && init.method !== 'GET'
      ? jsonResponse(paymentMethodFixture({ id: 'pm9', label: 'Moov Money' }), 201)
      : jsonResponse(METHODS),
  );
});
afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  show.mockReset();
  vi.unstubAllGlobals();
});

const render = () =>
  renderWithCapabilities(withToast(<PaymentMethodsPage />, show), {
    permissions: ['sales.payment_method.manage'],
    sites: SITES,
  });

describe('moyens de paiement configurables', () => {
  it('liste : libellés de l’entreprise, type traduit, référence, disponibilité par site', async () => {
    render();
    const orange = (await screen.findByText('Orange Money')).closest('tr') as HTMLElement;
    expect(within(orange).getByText('Mobile Money')).toBeTruthy();
    expect(within(orange).getByText('Oui')).toBeTruthy();
    expect(within(orange).getByText('Manuelle')).toBeTruthy();
    expect(within(orange).getByText('Tous les sites')).toBeTruthy();
    const wave = screen.getByText('Wave').closest('tr') as HTMLElement;
    expect(within(wave).getByText('Désactivé sur : Dépôt')).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/MOBILE_MONEY|\bCASH\b/);
  });

  it('création : libellé, type, référence obligatoire ; aucune intégration API envoyée', async () => {
    render();
    await screen.findByText('Orange Money');
    fireEvent.click(screen.getByRole('button', { name: 'Nouveau moyen' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/^Libellé/), {
      target: { value: 'Moov Money' },
    });
    fireEvent.click(within(dialog).getByLabelText('Référence obligatoire'));
    await act(async () => {
      within(dialog).getByRole('button', { name: 'Enregistrer' }).click();
    });
    await waitFor(() => expect(writes('POST')).toHaveLength(1));
    const [url, init] = writes('POST')[0] ?? [];
    expect(String(url)).toContain('/api/v1/payment-methods');
    expect(JSON.parse(String(init?.body))).toEqual({
      label: 'Moov Money',
      kind: 'MOBILE_MONEY',
      reference_required: true,
      sort_order: 100,
    });
  });

  it('modification : type figé (jamais envoyé) ; disponibilité par site', async () => {
    render();
    const orange = (await screen.findByText('Orange Money')).closest('tr') as HTMLElement;
    fireEvent.click(within(orange).getByRole('button', { name: 'Modifier' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog.querySelector('#pm-kind')?.closest('.p-dropdown')?.className).toContain(
      'p-disabled',
    );
    await act(async () => {
      within(dialog).getByRole('button', { name: 'Enregistrer' }).click();
    });
    await waitFor(() => expect(writes('PATCH')).toHaveLength(1));
    const body = JSON.parse(String(writes('PATCH')[0]?.[1]?.body)) as Record<string, unknown>;
    expect(body).not.toHaveProperty('kind');
    cleanup();

    render();
    const wave = (await screen.findByText('Wave')).closest('tr') as HTMLElement;
    fireEvent.click(within(wave).getByRole('button', { name: 'Disponibilité par site' }));
    const sites = await screen.findByRole('dialog', { name: /Disponibilité de « Wave »/ });
    expect((within(sites).getByLabelText('Boutique') as HTMLInputElement).checked).toBe(true);
    expect((within(sites).getByLabelText('Dépôt') as HTMLInputElement).checked).toBe(false);
    fireEvent.click(within(sites).getByLabelText('Dépôt'));
    await waitFor(() => expect(writes('PUT')).toHaveLength(1));
    expect(String(writes('PUT')[0]?.[0])).toContain('/payment-methods/pm4/sites/s2');
    expect(JSON.parse(String(writes('PUT')[0]?.[1]?.body))).toEqual({ enabled: true });
  });
});
