// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { RefObject } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '@/core/i18n';
import { jsonResponse } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import { ConsoleAuthProvider } from './ConsoleAuth';
import { consoleRoutes } from './router';
import type { ConsoleActivation, ConsoleLicense, ConsolePayment, LicenseProposal } from './types';

const POSTE: ConsoleActivation = {
  id: 'act-1',
  tenant_id: 't-1',
  tenant_name: 'ABC Commerce',
  site_id: 'site-1',
  site_name: 'Boutique',
  subscription_id: 's-1',
  license_id: 'l-1',
  license_number: 'LIC-2026-00001',
  installation_id: '8f6b2c1e-0000-4000-8000-000000000001',
  label: 'Caisse 1',
  client_version: '1.0.0',
  status: 'ACTIVE',
  activated_at: '2026-09-26T11:00:00Z',
  last_seen_at: '2026-09-26T12:00:00Z',
  released_at: null,
  release_source: null,
  release_reason: null,
};

const ADMIN = { id: 'a1', email: 'admin@technova.example', full_name: 'Awa Admin' };

const PAYMENT: ConsolePayment = {
  id: 'p-1',
  tenant_id: 't-1',
  tenant_name: 'ABC Commerce',
  subscription_id: 's-1',
  plan_code: 'STANDARD',
  site_id: 'site-1',
  site_name: 'Boutique',
  amount: '10000.00',
  currency: 'XOF',
  period_start: '2026-10-01',
  period_end: '2026-11-01',
  payment_method: 'BANK_TRANSFER',
  declared_reference: 'VIR-001',
  requested_activations: null,
  status: 'CONFIRMED',
  created_at: '2026-09-25T10:00:00Z',
  decided_at: '2026-09-26T08:00:00Z',
  decided_by_email: 'admin@technova.example',
  rejection_reason: null,
};

const PROPOSAL: LicenseProposal = {
  payment_id: 'p-1',
  payment_status: 'CONFIRMED',
  tenant_id: 't-1',
  tenant_name: 'ABC Commerce',
  subscription_id: 's-1',
  site_id: 'site-1',
  site_name: 'Boutique',
  plan_code: 'STANDARD',
  billing_period: 'annual',
  timezone: 'Africa/Ouagadougou',
  valid_from: '2026-10-01',
  valid_until: '2027-09-30',
  initial_requested_activations: 2,
  current_activations: null,
  requested_activations: null,
  max_activations: 2,
  grace_continuity: false,
  payment_period_start: '2026-10-01',
  payment_period_end: '2027-09-30',
  blocking: null,
  license_id: null,
};

const LICENSE: ConsoleLicense = {
  id: 'l-1',
  license_number: 'LIC-2026-00001',
  license_version: 1,
  supersedes_id: null,
  superseded_by_id: null,
  tenant_id: 't-1',
  tenant_name: 'ABC Commerce',
  site_id: 'site-1',
  site_name: 'Boutique',
  site_code: 'BTQ',
  subscription_id: 's-1',
  payment_id: 'p-1',
  plan_code: 'STANDARD',
  billing_period: 'annual',
  valid_from: '2026-10-01',
  valid_until: '2027-09-30',
  timezone: 'Africa/Ouagadougou',
  max_activations: 3,
  activations_used: 1,
  modules: ['catalog', 'sales'],
  features: [],
  limits: { max_users: 5 },
  status: 'ISSUED',
  state: 'ACTIVE',
  issued_at: '2026-09-26T10:00:00Z',
  issued_by_email: 'admin@technova.example',
  key_id: 'technova-ed25519-2026-01',
  payload_sha256: 'ab'.repeat(32),
  revoked_at: null,
  revoked_by_email: null,
  revocation_reason: null,
};

const REVOKED: ConsoleLicense = {
  ...LICENSE,
  id: 'l-2',
  license_number: 'LIC-2026-00002',
  status: 'REVOKED',
  state: 'REVOKED',
  revoked_at: '2026-09-27T10:00:00Z',
  revoked_by_email: 'admin@technova.example',
  revocation_reason: 'Fraude',
};

const fetchMock = vi.fn<typeof fetch>();
const show = vi.fn();

function renderConsole(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(consoleRoutes, { initialEntries: [path] });
  const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  render(
    <QueryClientProvider client={queryClient}>
      <ToastContext.Provider value={toast}>
        <ConsoleAuthProvider>
          <RouterProvider router={router} />
        </ConsoleAuthProvider>
      </ToastContext.Provider>
    </QueryClientProvider>,
  );
  return router;
}

function consoleApi({
  proposal = PROPOSAL,
  onPost,
}: {
  proposal?: LicenseProposal;
  onPost?: (url: string, body: Record<string, unknown>) => Response | Promise<Response>;
} = {}) {
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (u.endsWith('/me')) return jsonResponse(ADMIN);
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      if (onPost) return onPost(u, body);
      return jsonResponse({ ...LICENSE, id: 'l-new', license_number: 'LIC-2026-00009' }, 201);
    }
    if (u.endsWith('/payments/p-1')) return jsonResponse(PAYMENT);
    if (u.endsWith('/payments/p-1/license-proposal')) return jsonResponse(proposal);
    if (u.endsWith('/licenses/l-1')) return jsonResponse(LICENSE);
    if (u.endsWith('/licenses/l-2')) return jsonResponse(REVOKED);
    if (/\/licenses\/l-new$/.test(u)) {
      return jsonResponse({ ...LICENSE, id: 'l-new', license_number: 'LIC-2026-00009' });
    }
    if (u.includes('/activations?subscription_id=s-1')) {
      return jsonResponse({ items: [POSTE], total: 1, limit: 100, offset: 0 });
    }
    if (u.includes('/licenses?')) {
      return jsonResponse({ items: [LICENSE, REVOKED], total: 2, limit: 25, offset: 0 });
    }
    if (u.endsWith('/plans')) return jsonResponse([]);
    if (u.includes('/audit')) return jsonResponse({ items: [], total: 0, limit: 20, offset: 0 });
    return jsonResponse({ code: 'not_found' }, 404);
  });
}

const posts = () =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'POST')
    .map(([u, init]) => [String(u), JSON.parse(String(init?.body)) as unknown] as const);
const urls = () => fetchMock.mock.calls.map(([u]) => String(u));

async function submitDialog(name: string, reason: string) {
  const form = await screen.findByRole('form', { name });
  const submit = within(form).getByRole('button', {
    name: /^(Générer la licence|Révoquer|Réémettre)$/,
  });
  expect((submit as HTMLButtonElement).disabled).toBe(true); // confirmation explicite exigée
  fireEvent.change(within(form).getByLabelText(/Raison/), { target: { value: reason } });
  fireEvent.click(within(form).getByLabelText('Je confirme cette action.'));
  fireEvent.click(submit);
  return form;
}

describe('Console TechNova : licences', () => {
  beforeEach(() => vi.stubGlobal('fetch', fetchMock));
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    show.mockReset();
    vi.unstubAllGlobals();
  });

  it('liste : numéro, entreprise, validité, postes, état ; filtre d’état transmis au serveur', async () => {
    consoleApi();
    renderConsole('/tech-admin/licenses');
    const row = (await screen.findByText('LIC-2026-00001')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Active')).toBeTruthy();
    expect(within(row).getByText('3')).toBeTruthy();
    expect(row.textContent).toContain('1 oct. 2026');
    const revoked = screen.getByText('LIC-2026-00002').closest('tr') as HTMLElement;
    expect(within(revoked).getByText('Révoquée')).toBeTruthy();

    fireEvent.click(screen.getByTestId('filter-license-state'));
    const panel = await waitFor(() => {
      const found = document.querySelector('.p-dropdown-panel');
      if (!found) throw new Error('panneau fermé');
      return found as HTMLElement;
    });
    fireEvent.click(within(panel).getByText('Révoquée'));
    await waitFor(() => expect(urls().some((u) => u.includes('state=REVOKED'))).toBe(true));
  });

  it('paiement confirmé : génération avec postes proposés puis ajustés, raison et confirmation', async () => {
    consoleApi();
    const router = renderConsole('/tech-admin/payments/p-1');
    const card = await screen.findByTestId('payment-license');
    expect(await within(card).findByText(/1 oct\. 2026/)).toBeTruthy();
    fireEvent.click(await within(card).findByRole('button', { name: 'Générer la licence' }));
    const form = await screen.findByRole('form', { name: 'Générer la licence' });
    const activations = within(form).getByLabelText(/Postes autorisés/) as HTMLInputElement;
    expect(activations.value).toBe('2'); // postes demandés à la souscription
    fireEvent.change(activations, { target: { value: '3' } });
    fireEvent.blur(activations);
    await submitDialog('Générer la licence', 'Virement vérifié');
    await waitFor(() => expect(posts()).toHaveLength(1));
    const [url, body] = posts()[0]!;
    expect(url).toMatch(/\/payments\/p-1\/license$/);
    expect(body).toEqual({ reason: 'Virement vérifié', max_activations: 3 });
    await waitFor(() => expect(router.state.location.pathname).toBe('/tech-admin/licenses/l-new'));
  });

  it('renouvellement : postes actuels, demande explicite, continuité de grâce', async () => {
    consoleApi({
      proposal: {
        ...PROPOSAL,
        current_activations: 3,
        requested_activations: 5,
        max_activations: 5,
        grace_continuity: true,
      },
    });
    renderConsole('/tech-admin/payments/p-1');
    const card = await screen.findByTestId('payment-license');
    expect((await within(card).findByTestId('proposal-current')).textContent).toBe('3');
    expect(within(card).getByTestId('proposal-request').textContent).toBe('5');
    expect(within(card).getByTestId('proposal-max').textContent).toBe('5');
    expect(within(card).getByTestId('grace-continuity').textContent).toContain('aucun jour perdu');
    expect(within(card).queryByTestId('proposal-initial')).toBeNull();
    fireEvent.click(within(card).getByRole('button', { name: 'Générer la licence' }));
    const form = await screen.findByRole('form', { name: 'Générer la licence' });
    expect((within(form).getByLabelText(/Postes autorisés/) as HTMLInputElement).value).toBe('5');
  });

  it('renouvellement sans demande : reconduction affichée', async () => {
    consoleApi({ proposal: { ...PROPOSAL, current_activations: 4, max_activations: 4 } });
    renderConsole('/tech-admin/payments/p-1');
    const request = await screen.findByTestId('proposal-request');
    expect(request.textContent).toBe('Reconduction (aucun changement demandé)');
  });

  it('génération impossible : raison du serveur affichée, aucun bouton', async () => {
    consoleApi({ proposal: { ...PROPOSAL, blocking: 'tenant_suspended' } });
    renderConsole('/tech-admin/payments/p-1');
    const blocking = await screen.findByTestId('license-blocking');
    expect(blocking.textContent).toContain('suspendue');
    expect(screen.queryByRole('button', { name: 'Générer la licence' })).toBeNull();
  });

  it('licence déjà générée : lien vers la licence', async () => {
    consoleApi({
      proposal: { ...PROPOSAL, blocking: 'license_already_issued', license_id: 'l-1' },
    });
    renderConsole('/tech-admin/payments/p-1');
    const link = await screen.findByRole('link', { name: 'Ouvrir la licence' });
    expect(link.getAttribute('href')).toBe('/tech-admin/licenses/l-1');
  });

  it('erreur du Signing Service : message traduit, rien de créé', async () => {
    consoleApi({
      onPost: () => jsonResponse({ code: 'signing_service_unavailable' }, 503),
    });
    renderConsole('/tech-admin/payments/p-1');
    fireEvent.click(await screen.findByRole('button', { name: 'Générer la licence' }));
    const form = await submitDialog('Générer la licence', 'Virement vérifié');
    expect(await within(form).findByText(/service de signature est indisponible/)).toBeTruthy();
  });

  it('révocation : raison obligatoire, confirmation, envoi', async () => {
    consoleApi({ onPost: () => jsonResponse(REVOKED) });
    renderConsole('/tech-admin/licenses/l-1');
    expect(await screen.findByText('Licence LIC-2026-00001')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Révoquer' }));
    const form = await screen.findByRole('form', { name: 'Révoquer la licence' });
    fireEvent.click(within(form).getByLabelText('Je confirme cette action.'));
    fireEvent.click(within(form).getByRole('button', { name: 'Révoquer' }));
    expect(await within(form).findByText('La raison est obligatoire.')).toBeTruthy();
    expect(posts()).toHaveLength(0);
    fireEvent.change(within(form).getByLabelText(/Raison/), { target: { value: 'Fraude' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Révoquer' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toEqual([
      expect.stringMatching(/\/licenses\/l-1\/revoke$/),
      { reason: 'Fraude' },
    ]);
  });

  it('réémission : postes conservés sauf changement explicite, nouvelle licence ouverte', async () => {
    consoleApi();
    const router = renderConsole('/tech-admin/licenses/l-1');
    fireEvent.click(await screen.findByRole('button', { name: 'Réémettre' }));
    await submitDialog('Réémettre la licence', 'Fichier compromis');
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]![1]).toEqual({ reason: 'Fichier compromis' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/tech-admin/licenses/l-new'));
  });

  it('licence révoquée : motif affiché, ni téléchargement ni révocation', async () => {
    consoleApi();
    renderConsole('/tech-admin/licenses/l-2');
    expect((await screen.findByTestId('license-revocation-reason')).textContent).toBe('Fraude');
    expect(screen.queryByRole('button', { name: /Télécharger/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Révoquer' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Réémettre' })).toBeTruthy();
  });

  it('postes de la licence : utilisés / autorisés, libération par TechNova avec raison', async () => {
    consoleApi({ onPost: () => jsonResponse({ ...POSTE, status: 'RELEASED' }) });
    renderConsole('/tech-admin/licenses/l-1');
    const postes = await screen.findByTestId('license-postes');
    expect(postes.textContent).toContain('Postes (1 / 3)');
    expect(await within(postes).findByText('Caisse 1')).toBeTruthy();
    fireEvent.click(within(postes).getByRole('button', { name: 'Libérer Caisse 1' }));
    const form = await screen.findByRole('form', { name: 'Libérer le poste' });
    fireEvent.change(within(form).getByLabelText(/Raison/), { target: { value: 'PC volé' } });
    fireEvent.click(within(form).getByLabelText('Je confirme cette action.'));
    fireEvent.click(within(form).getByRole('button', { name: 'Libérer' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toEqual([
      expect.stringMatching(/\/activations\/act-1\/release$/),
      { reason: 'PC volé' },
    ]);
  });
});
