// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';

import InventoriesPage from './InventoriesPage';
import { ALL_PERMISSIONS, inventory } from './testData';

const listCalls = (fetchMock: ReturnType<typeof vi.fn<typeof fetch>>) =>
  fetchMock.mock.calls.map(([url]) => String(url)).filter((u) => u.includes('/inventories?'));

describe('liste des inventaires', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async () =>
      pageOf([
        inventory(),
        inventory({
          id: 'i2',
          number: 'INV-000002',
          status: 'VALIDATED',
          inventory_type: 'FULL',
          counted_count: 3,
          line_count: 3,
          variance_count: 2,
        }),
      ]),
    );
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('affiche numéros, types, progression, écarts et statuts traduits', async () => {
    renderWithCapabilities(<InventoriesPage />, { permissions: ALL_PERMISSIONS });
    expect(await screen.findByText('INV-000001')).toBeTruthy();
    const counting = screen.getByText('INV-000001').closest('tr') as HTMLElement;
    expect(within(counting).getByText('Comptage en cours')).toBeTruthy();
    expect(within(counting).getByText('Ciblé')).toBeTruthy();
    expect(within(counting).getByText('1 / 2')).toBeTruthy();
    const validated = screen.getByText('INV-000002').closest('tr') as HTMLElement;
    expect(within(validated).getByText('Validé')).toBeTruthy();
    expect(within(validated).getByText('Complet')).toBeTruthy();
    expect(within(validated).getByText('2')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Nouvel inventaire' }).length).toBeGreaterThan(0);
  });

  it('filtres serveur (statut, type) et réinitialisation', async () => {
    renderWithCapabilities(<InventoriesPage />, { permissions: ALL_PERMISSIONS });
    await screen.findByText('INV-000001');
    const reset = screen.getByRole('button', { name: 'Réinitialiser' });
    expect((reset as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'INV-2' } });
    await waitFor(() => expect(listCalls(fetchMock).at(-1)).toContain('search=INV-2'));
    expect(listCalls(fetchMock).at(-1)).toContain('sort=-number');
    expect((reset as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(reset);
    await waitFor(() => expect(listCalls(fetchMock).at(-1)).not.toContain('search='));
  });

  it('consultation seule : aucune création ; état vide sans action', async () => {
    fetchMock.mockImplementation(async () => pageOf([]));
    renderWithCapabilities(<InventoriesPage />, {
      permissions: ['inventory_count.inventory.view'],
    });
    expect(await screen.findByText('Aucun inventaire pour le moment.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Nouvel inventaire' })).toBeNull();
  });

  it('erreur de chargement : message traduit, jamais d’erreur technique', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse({ code: 'permission_denied', detail: 'Permission insuffisante' }, 403),
    );
    renderWithCapabilities(<InventoriesPage />, { permissions: ALL_PERMISSIONS });
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByText(/403/)).toBeNull();
  });
});
