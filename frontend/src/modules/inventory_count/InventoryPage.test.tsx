// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import type { Inventory, InventoryLine } from './api';
import InventoryPage from './InventoryPage';
import { ALL_PERMISSIONS, inventory, line, summary, uncounted } from './testData';

function withToast(element: ReactNode, show: ReturnType<typeof vi.fn>) {
  const ref = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const calls = (fetchMock: ReturnType<typeof vi.fn<typeof fetch>>, method: string) =>
  fetchMock.mock.calls.filter(([, init]) => (init?.method ?? 'GET') === method);

describe('fiche inventaire', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const show = vi.fn();
  let current: Inventory;
  let lines: InventoryLine[];

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    current = inventory();
    lines = [line(), uncounted];
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      const method = init?.method ?? 'GET';
      if (method === 'PATCH') return jsonResponse({ lines: [line()], summary: summary() });
      if (method === 'POST' && u.endsWith('/validate')) {
        return jsonResponse({ ...current, status: 'VALIDATED' });
      }
      if (method === 'POST' && u.endsWith('/start')) {
        current = inventory();
        return jsonResponse(current);
      }
      if (u.includes('/lines')) return pageOf(lines);
      return jsonResponse(current);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  const render = (permissions = ALL_PERMISSIONS) =>
    renderWithCapabilities(withToast(<InventoryPage />, show), {
      permissions,
      path: '/inventories/:id',
      route: '/inventories/i1',
    });

  it('comptage : progression, écart indicatif, saisie enregistrée ligne par ligne', async () => {
    render();
    expect(await screen.findByRole('heading', { name: 'Inventaire INV-000001' })).toBeTruthy();
    expect(screen.getByText('Comptage en cours')).toBeTruthy();
    expect(screen.getByText('Articles comptés : 1 / 2')).toBeTruthy();
    // Terminer impossible tant que tout n'est pas compté.
    const complete = screen.getByRole('button', { name: 'Terminer le comptage' });
    expect((complete as HTMLButtonElement).disabled).toBe(true);

    const row = (await screen.findByText('RIZ-25')).closest('tr') as HTMLElement;
    // Écart indicatif (physique − théorique initial) et écart appliqué (stock courant).
    expect(within(row).getByText('-5')).toBeTruthy();
    expect(within(row).getByText('Manquant')).toBeTruthy();
    expect(within(row).getByText('Stock actuel : 105')).toBeTruthy();
    expect(within(row).getByText('Écart appliqué à la validation : -10')).toBeTruthy();

    const input = screen.getByLabelText('Quantité physique de HUILE-5') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '11,5' } });
    fireEvent.blur(input);
    await waitFor(() => expect(calls(fetchMock, 'PATCH')).toHaveLength(1));
    const [url, init] = calls(fetchMock, 'PATCH')[0] ?? [];
    expect(String(url)).toContain('/inventories/i1/lines');
    expect(JSON.parse(String(init?.body))).toEqual({
      counts: [{ line_id: 'l2', quantity_physical: '11.5' }],
    });
  });

  it('saisie invalide : message, aucun envoi ; Entrée passe à la ligne suivante', async () => {
    render();
    const first = (await screen.findByLabelText('Quantité physique de RIZ-25')) as HTMLInputElement;
    expect(first.value).toBe('95');
    fireEvent.change(first, { target: { value: '-3' } });
    fireEvent.keyDown(first, { key: 'Enter' });
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(first.getAttribute('aria-invalid')).toBe('true');
    expect(calls(fetchMock, 'PATCH')).toHaveLength(0);
    expect(document.activeElement).toBe(screen.getByLabelText('Quantité physique de HUILE-5'));
  });

  it('à valider : résumé, confirmation expliquant l’ajustement, puis validation', async () => {
    current = inventory({
      status: 'READY_TO_VALIDATE',
      counted_count: 2,
      completed_at: '2026-09-24T09:00:00Z',
      summary: summary({ counted: 2, surplus: 1, shortage: 1, no_variance: 0 }),
    });
    lines = [
      line(),
      line({
        ...uncounted,
        quantity_physical: '13.000',
        indicative_variance: '1.000',
        quantity_variance: '1.000',
      }),
    ];
    render();
    const summaryRegion = await screen.findByRole('region', { name: 'Résumé des écarts' });
    expect(within(summaryRegion).getByText('Excédents')).toBeTruthy();
    expect(within(summaryRegion).getByText('2 / 2')).toBeTruthy();
    // Lecture seule des quantités à ce stade.
    expect(screen.queryByLabelText('Quantité physique de RIZ-25')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: "Valider l'inventaire" }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Articles : 2 · Comptés : 2 · Excédents : 1/)).toBeTruthy();
    expect(within(dialog).getByText(/applique les ajustements au stock/)).toBeTruthy();
    expect(calls(fetchMock, 'POST')).toHaveLength(0); // rien avant confirmation
    fireEvent.click(within(dialog).getByRole('button', { name: "Valider l'inventaire" }));
    await waitFor(() => expect(calls(fetchMock, 'POST')).toHaveLength(1));
    expect(String(calls(fetchMock, 'POST')[0]?.[0])).toContain('/inventories/i1/validate');
  });

  it('validé : lecture seule, stock à la validation, valeur, lien vers les mouvements', async () => {
    current = inventory({
      status: 'VALIDATED',
      counted_count: 1,
      line_count: 1,
      validated_at: '2026-09-24T10:00:00Z',
      validated_by_name: 'Moussa',
      summary: summary({
        lines: 1,
        counted: 1,
        final: true,
        shortage_value: '10000.00',
        adjustment_value: '-10000.00',
      }),
    });
    lines = [
      line({
        stock_current: null,
        stock_theoretical_at_validation: '105.000',
        unit_cost: '1000.0000',
      }),
    ];
    render();
    expect(await screen.findByText('Validé')).toBeTruthy();
    expect(screen.getByText(/lecture seule/)).toBeTruthy();
    expect(screen.getByRole('link', { name: "Voir les mouvements d'ajustement" })).toBeTruthy();
    expect(await screen.findByText('105 sac')).toBeTruthy(); // stock à la validation
    const row = screen.getByText('RIZ-25').closest('tr') as HTMLElement;
    expect(within(row).getByText('-10')).toBeTruthy(); // écart figé (et non −5)
    expect(screen.queryByRole('textbox', { name: /Quantité physique/ })).toBeNull();
    for (const name of ["Valider l'inventaire", "Annuler l'inventaire", 'Reprendre le comptage']) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
  });

  it('consultation seule (permission view) : aucune action, quantités non éditables', async () => {
    render(['inventory_count.inventory.view']);
    expect(await screen.findByText('RIZ-25')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Terminer le comptage' })).toBeNull();
    expect(screen.queryByRole('button', { name: "Annuler l'inventaire" })).toBeNull();
    expect(screen.queryByLabelText('Quantité physique de RIZ-25')).toBeNull();
  });

  it('brouillon : démarrage du comptage ; annulation avec motif obligatoire', async () => {
    current = inventory({
      status: 'DRAFT',
      started_at: null,
      started_by_name: null,
      summary: null,
    });
    render();
    fireEvent.click(await screen.findByRole('button', { name: 'Démarrer le comptage' }));
    await waitFor(() => expect(calls(fetchMock, 'POST')).toHaveLength(1));
    expect(String(calls(fetchMock, 'POST')[0]?.[0])).toContain('/inventories/i1/start');
    // Régression : le tableau passe en mode comptage (colonnes propres au statut).
    expect(await screen.findByLabelText('Quantité physique de RIZ-25')).toBeTruthy();
    expect(screen.queryByRole('button', { name: "Retirer l'article" })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: "Annuler l'inventaire" }));
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: "Annuler l'inventaire" });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/Motif d'annulation/), {
      target: { value: 'Recomptage prévu' },
    });
    expect((confirm as HTMLButtonElement).disabled).toBe(false);
  });

  it('erreur de chargement : message traduit et « Réessayer »', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse({ code: 'inventory_not_found', detail: 'x' }, 404),
    );
    render();
    expect(await screen.findByText('Inventaire introuvable.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Réessayer' })).toBeTruthy();
  });
});
