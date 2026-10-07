// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities, SITES } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { ExitPage } from './DocumentPage';
import TransferPage from './TransferPage';

/**
 * Brouillons de documents de stock : un seul brouillon créé, validation de la version
 * effectivement enregistrée (régressions observées en E2E sur les sorties par lot).
 */

const LOTS = {
  article_id: 'a1',
  lot_tracked: true,
  expiry_tracked: false,
  lots: ['A', 'B'].map((n) => ({
    lot_id: `lot${n}`,
    number: n,
    quantity: '60.000',
    expiry_date: null,
    manufacturing_date: null,
    state: 'no_expiry',
    expired: false,
    created_at: '2026-10-01T08:00:00Z',
  })),
};

const exitDoc = (over: Record<string, unknown> = {}) => ({
  id: 'x1',
  number: 'SOR-000001',
  site_id: 's1',
  site_name: 'Boutique',
  status: 'DRAFT',
  operation_date: '2026-10-02',
  comment: null,
  total_amount: null,
  line_count: 0,
  created_at: '2026-10-02T08:00:00Z',
  created_by_name: 'Awa',
  validated_at: null,
  validated_by_name: null,
  cancelled_at: null,
  cancelled_by_name: null,
  cancellation_reason: null,
  reason_id: 'r1',
  reason_label: 'Perte',
  beneficiary: null,
  reference: null,
  lines: [],
  ...over,
});

const lotLine = {
  id: 'l1',
  line_no: 1,
  article_id: 'a1',
  article_reference: 'EAU-1',
  article_designation: 'Eau minérale',
  unit: 'bouteille',
  quantity: '100.000',
  unit_cost: null,
  amount: null,
  packaging_id: null,
  packaging_name: null,
  packaging_conversion: null,
  base_quantity: '100.000',
  location_name: null,
  lots: [{ lot_id: 'lotA', lot_number: 'A', expiry_date: null, state: 'ok', quantity: '60.000' }],
};

function withToast(element: ReactNode) {
  const ref = { current: { show: vi.fn() } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={ref}>{element}</ToastContext.Provider>;
}

const fetchMock = vi.fn<typeof fetch>();
/** Appels d'écriture dans l'ordre : « POST /stock/exits », « PUT /stock/exits/x1 »… */
const writes = () =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method && init.method !== 'GET')
    .map(([url, init]) => `${init?.method} ${String(url).replace(/^.*\/api\/v1/, '')}`);
const bodyOf = (call: string) => {
  const found = fetchMock.mock.calls.find(
    ([url, init]) => `${init?.method} ${String(url).replace(/^.*\/api\/v1/, '')}` === call,
  );
  return JSON.parse(String(found?.[1]?.body)) as Record<string, unknown>;
};

const chooseOption = async (label: RegExp, option: string) => {
  const field = screen.getByLabelText(label, { selector: 'input, select, span, div' });
  fireEvent.click(field.closest('.p-dropdown') ?? field);
  fireEvent.click((await screen.findAllByRole('option', { name: option, hidden: true })).at(-1)!);
};

/** Bouton de validation puis confirmation (dialogue unique de la coquille). */
const confirmValidation = async (label = 'Valider') => {
  fireEvent.click(screen.getAllByRole('button', { name: label })[0]!);
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getAllByRole('button', { name: label }).at(-1)!);
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
};

const EXIT_PERMISSIONS = [
  'stock.exit.view',
  'stock.exit.create',
  'stock.exit.update',
  'stock.exit.validate',
];

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe('brouillons de documents de stock', () => {
  /** Serveur simulé d'une sortie : création, mise à jour, validation (refus possible). */
  function exitServer(initial: Record<string, unknown> | null, refuseFirstValidation = false) {
    let current = initial;
    let refusals = refuseFirstValidation ? 1 : 0;
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      const method = init?.method ?? 'GET';
      if (u.includes('/stock/exit-reasons')) {
        return pageOf([{ id: 'r1', code: 'PERTE', label: 'Perte', is_active: true }]);
      }
      if (u.includes('/stock/available-lots')) return jsonResponse(LOTS);
      if (method === 'POST' && u.endsWith('/stock/exits')) {
        current = exitDoc({ ...(JSON.parse(String(init?.body)) as object) });
        return jsonResponse(current, 201);
      }
      if (method === 'PUT') {
        current = { ...current, ...(JSON.parse(String(init?.body)) as object) };
        return jsonResponse(current);
      }
      if (method === 'POST' && u.endsWith('/validate')) {
        if (refusals > 0) {
          refusals -= 1;
          return jsonResponse(
            {
              status: 409,
              title: 'Conflit',
              detail: 'La répartition par lot ne correspond pas',
              code: 'lot_allocation_incomplete',
            },
            409,
          );
        }
        current = { ...current, status: 'VALIDATED', validated_at: '2026-10-02T09:00:00Z' };
        return jsonResponse(current);
      }
      if (u.includes('/stock/exits/x1')) return jsonResponse(current);
      return pageOf([]);
    });
  }

  it('brouillon créé puis « Valider » sur le formulaire encore affiché : un seul brouillon, validé par son identifiant', async () => {
    exitServer(null);
    // Le formulaire « nouveau » reste affiché après la création (navigation en cours) : c'est
    // la fenêtre dans laquelle un second POST créait un second brouillon.
    renderWithCapabilities(withToast(<ExitPage />), {
      permissions: EXIT_PERMISSIONS,
      sites: [SITES[0]!],
      path: '/stock/exits/*',
      route: '/stock/exits/new',
    });
    await screen.findByRole('heading', { name: 'Nouvelle sortie' });
    await chooseOption(/^Motif/, 'Perte');
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    await waitFor(() => expect(writes()).toEqual(['POST /stock/exits']));
    await confirmValidation();
    await waitFor(() =>
      expect(writes()).toEqual(['POST /stock/exits', 'POST /stock/exits/x1/validate']),
    );
  });

  it('double clic sur « Enregistrer le brouillon » : une seule création', async () => {
    exitServer(null);
    renderWithCapabilities(withToast(<ExitPage />), {
      permissions: EXIT_PERMISSIONS,
      sites: [SITES[0]!],
      path: '/stock/exits/*',
      route: '/stock/exits/new',
    });
    await screen.findByRole('heading', { name: 'Nouvelle sortie' });
    await chooseOption(/^Motif/, 'Perte');
    const save = screen.getByRole('button', { name: 'Enregistrer le brouillon' });
    fireEvent.click(save);
    fireEvent.click(save);
    await waitFor(() => expect(writes()).toEqual(['POST /stock/exits']));
    // Toujours un seul envoi une fois la première soumission terminée.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(writes()).toEqual(['POST /stock/exits']);
  });

  it('brouillon existant modifié puis validé : la modification est enregistrée avant la validation', async () => {
    exitServer(exitDoc({ lines: [lotLine], line_count: 1 }));
    renderWithCapabilities(withToast(<ExitPage />), {
      permissions: EXIT_PERMISSIONS,
      path: '/stock/exits/:id',
      route: '/stock/exits/x1',
    });
    fireEvent.change(await screen.findByLabelText('Quantité du lot B'), {
      target: { value: '40' },
    });
    await confirmValidation();
    await waitFor(() =>
      expect(writes()).toEqual(['PUT /stock/exits/x1', 'POST /stock/exits/x1/validate']),
    );
    expect((bodyOf('PUT /stock/exits/x1').lines as { lots: unknown[] }[])[0]?.lots).toEqual([
      { lot_id: 'lotA', quantity: '60.000' },
      { lot_id: 'lotB', quantity: '40' },
    ]);
  });

  it('validation refusée, modification, nouvelle validation : même brouillon mis à jour puis validé', async () => {
    exitServer(exitDoc({ lines: [lotLine], line_count: 1 }), true);
    renderWithCapabilities(withToast(<ExitPage />), {
      permissions: EXIT_PERMISSIONS,
      path: '/stock/exits/:id',
      route: '/stock/exits/x1',
    });
    const lotB = await screen.findByLabelText('Quantité du lot B');
    await confirmValidation();
    await waitFor(() => expect(writes()).toEqual(['POST /stock/exits/x1/validate']));
    fireEvent.change(lotB, { target: { value: '40' } });
    await confirmValidation();
    await waitFor(() =>
      expect(writes()).toEqual([
        'POST /stock/exits/x1/validate',
        'PUT /stock/exits/x1',
        'POST /stock/exits/x1/validate',
      ]),
    );
  });

  it('brouillon inchangé : validé tel quel, sans réenregistrement', async () => {
    exitServer(exitDoc({ lines: [lotLine], line_count: 1 }));
    renderWithCapabilities(withToast(<ExitPage />), {
      permissions: EXIT_PERMISSIONS,
      path: '/stock/exits/:id',
      route: '/stock/exits/x1',
    });
    await screen.findByLabelText('Quantité du lot B');
    await confirmValidation();
    await waitFor(() => expect(writes()).toEqual(['POST /stock/exits/x1/validate']));
  });

  it('transfert existant modifié puis validé : la modification est enregistrée avant la validation', async () => {
    const transfer = {
      id: 't1',
      number: 'TRF-000001',
      source_site_id: 's1',
      source_site_name: 'Boutique',
      destination_site_id: 's2',
      destination_site_name: 'Dépôt',
      status: 'DRAFT',
      operation_date: '2026-10-02',
      comment: null,
      total_amount: null,
      line_count: 1,
      created_at: '2026-10-02T08:00:00Z',
      created_by_name: 'Awa',
      validated_at: null,
      validated_by_name: null,
      cancelled_at: null,
      cancelled_by_name: null,
      cancellation_reason: null,
      lines: [
        {
          id: 'l1',
          line_no: 1,
          article_id: 'a1',
          article_reference: 'RIZ-25',
          article_designation: 'Riz 25 kg',
          unit: 'sac',
          quantity: '20.000',
          unit_cost: null,
          amount: null,
        },
      ],
    };
    fetchMock.mockImplementation(async (url, init) => {
      const u = String(url);
      const method = init?.method ?? 'GET';
      if (method === 'PUT') return jsonResponse(transfer);
      if (method === 'POST' && u.endsWith('/validate')) {
        return jsonResponse({ ...transfer, status: 'VALIDATED' });
      }
      if (u.includes('/stock/transfers/t1')) return jsonResponse(transfer);
      return pageOf([]);
    });
    renderWithCapabilities(withToast(<TransferPage />), {
      permissions: [
        'stock.transfer.view',
        'stock.transfer.create',
        'stock.transfer.update',
        'stock.transfer.validate',
      ],
      features: ['stock.transfers'],
      path: '/stock/transfers/:id',
      route: '/stock/transfers/t1',
    });
    await screen.findByRole('heading', { name: 'Transfert TRF-000001' });
    fireEvent.change(screen.getByLabelText(/^Quantité \(sac\)/), { target: { value: '10' } });
    await confirmValidation('Valider le transfert');
    await waitFor(() =>
      expect(writes()).toEqual(['PUT /stock/transfers/t1', 'POST /stock/transfers/t1/validate']),
    );
    expect((bodyOf('PUT /stock/transfers/t1').lines as { quantity: string }[])[0]?.quantity).toBe(
      '10',
    );
  });
});
