// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntryPage, ExitPage } from './DocumentPage';
import { jsonResponse, pageOf, renderWithCapabilities } from './testing';

const validatedEntry = {
  id: 'e1',
  number: 'ENT-000001',
  site_id: 's1',
  site_name: 'Boutique',
  status: 'VALIDATED',
  operation_date: '2026-09-20',
  comment: null,
  total_amount: '15000.00',
  line_count: 1,
  created_at: '2026-09-20T08:00:00Z',
  created_by_name: 'Awa',
  validated_at: '2026-09-20T08:05:00Z',
  validated_by_name: 'Awa',
  cancelled_at: null,
  cancelled_by_name: null,
  cancellation_reason: null,
  kind: 'PURCHASE',
  supplier_id: 'f1',
  supplier_name: 'Faso Import',
  document_reference: 'BL-42',
  lines: [
    {
      id: 'l1',
      line_no: 1,
      article_id: 'a1',
      article_reference: 'VIS-001',
      article_designation: 'Vis à bois',
      unit: 'boîte',
      quantity: '10.000',
      unit_cost: '1500.00',
      amount: '15000.00',
    },
  ],
};

describe('document de stock', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('affiche une entrée validée en lecture seule, annulable avec la permission', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(validatedEntry));
    renderWithCapabilities(<EntryPage />, {
      permissions: ['stock.entry.view', 'stock.entry.update', 'stock.entry.cancel'],
      path: '/stock/entries/:id',
      route: '/stock/entries/e1',
    });
    expect(await screen.findByRole('heading', { name: 'Entrée ENT-000001' })).toBeTruthy();
    expect(screen.getByText('Validé')).toBeTruthy();
    expect(screen.getByText('Faso Import')).toBeTruthy();
    expect(screen.getByText('VIS-001 — Vis à bois')).toBeTruthy();
    // Document validé : jamais de formulaire de saisie.
    expect(screen.queryByRole('button', { name: 'Enregistrer le brouillon' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Annuler le document' }));
    const confirm = await screen.findAllByRole('button', { name: 'Annuler le document' });
    // Motif obligatoire (5 caractères) avant confirmation.
    expect((confirm.at(-1) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Motif d'annulation"), {
      target: { value: 'Erreur de saisie' },
    });
    expect((confirm.at(-1) as HTMLButtonElement).disabled).toBe(false);
  });

  it("sans permission d'annulation, aucune action sur une entrée validée", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(validatedEntry));
    renderWithCapabilities(<EntryPage />, {
      permissions: ['stock.entry.view'],
      path: '/stock/entries/:id',
      route: '/stock/entries/e1',
    });
    expect(await screen.findByText('Validé')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Annuler le document' })).toBeNull();
  });

  it('nouvelle sortie : motif requis, site à choisir, validation selon la permission', async () => {
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/stock/exit-reasons')
        ? pageOf([
            {
              id: 'r1',
              code: 'PERTE',
              label: 'Perte',
              description: null,
              is_system: true,
              is_active: true,
            },
          ])
        : pageOf([]),
    );
    renderWithCapabilities(<ExitPage />, {
      permissions: ['stock.exit.view', 'stock.exit.create'],
      path: '/stock/exits/new',
      route: '/stock/exits/new',
    });
    expect(await screen.findByRole('heading', { name: 'Nouvelle sortie' })).toBeTruthy();
    expect(screen.getAllByText('Choisir un site').length).toBeGreaterThan(0); // « tous les sites »
    expect(screen.queryByRole('button', { name: 'Valider' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }));
    // Site et motif obligatoires : aucun envoi au serveur.
    expect((await screen.findAllByText('Champ obligatoire')).length).toBe(2);
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false),
    );
  });
});
