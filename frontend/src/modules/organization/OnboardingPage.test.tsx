// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities } from '@/shared/testing';

import type { Onboarding, OnboardingStep } from './onboardingApi';
import OnboardingPage from './OnboardingPage';

const step = (
  code: string,
  required: boolean,
  status: OnboardingStep['status'],
  action: Partial<NonNullable<OnboardingStep['action']>> | null = null,
): OnboardingStep => ({
  code,
  order: 0,
  required,
  title: `onboarding.steps.${code}.title`,
  description: `onboarding.steps.${code}.description`,
  status,
  completed_at: status === 'COMPLETED' ? '2026-09-25T10:00:00Z' : null,
  action: action && {
    route: '/organization/sites?create=1',
    label: `onboarding.actions.${code}`,
    permission: 'organization.site.manage',
    available: true,
    blocked_reason: null,
    ...action,
  },
});

const ONBOARDING: Onboarding = {
  status: 'IN_PROGRESS',
  completed: false,
  progress: { completed: 4, total: 8, percentage: 50, required_completed: 4, required_total: 5 },
  current_step: 'first_site',
  next_action: null,
  subscription_status: 'pending_activation',
  steps: [
    step('account', true, 'COMPLETED'),
    step('company', true, 'COMPLETED', { route: '/organization/company' }),
    step('business_profile', true, 'COMPLETED', { route: '/organization/company' }),
    step('subscription', true, 'COMPLETED', { route: '/subscription' }),
    step('first_site', true, 'NOT_STARTED', {}),
    step('catalogue', false, 'NOT_STARTED', {
      route: '/catalog/articles?create=1',
      available: false,
      blocked_reason: 'subscription_restricted',
    }),
    step('users', false, 'NOT_STARTED', { route: '/users/members?create=1' }),
    step('configuration', false, 'IN_PROGRESS', { route: '/organization/company' }),
  ],
};

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

describe("page d'installation", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (_url, init) => {
      if (init?.method === 'PATCH')
        return jsonResponse({
          ...ONBOARDING,
          steps: ONBOARDING.steps.map((s) =>
            s.code === 'first_site' ? { ...s, status: 'IN_PROGRESS' } : s,
          ),
        });
      return jsonResponse(ONBOARDING);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  const render = (
    permissions = ['organization.onboarding.view', 'organization.onboarding.manage'],
  ) =>
    renderWithCapabilities(<OnboardingPage />, {
      permissions,
      path: '/onboarding',
      route: '/onboarding',
      extraRoutes: [
        { path: '/organization/sites', element: <Where /> },
        { path: '/users/members', element: <Where /> },
      ],
    });

  it('affiche la progression, les étapes, leur statut et leur caractère', async () => {
    render();
    expect(await screen.findByText('Installation terminée à 50 %')).toBeTruthy();
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('50');
    expect(screen.getByText(/4 étapes terminées sur 8/)).toBeTruthy();
    expect(screen.getByText(/étapes obligatoires : 4\/5/)).toBeTruthy();

    const steps = within(screen.getByRole('list', { name: "Étapes de l'installation" }));
    const items = steps.getAllByRole('listitem');
    expect(items).toHaveLength(8);
    const company = within(screen.getByTestId('step-company'));
    expect(company.getByText('Votre entreprise')).toBeTruthy();
    expect(company.getByText('Obligatoire')).toBeTruthy();
    expect(company.getByText('Terminée')).toBeTruthy();
    // Étape terminée : plus d'action.
    expect(company.queryByRole('button')).toBeNull();
    const current = screen.getByTestId('step-first_site');
    expect(current.getAttribute('aria-current')).toBe('step');
    expect(within(current).getByText('À faire')).toBeTruthy();
    const configuration = within(screen.getByTestId('step-configuration'));
    expect(configuration.getByText('Recommandée')).toBeTruthy();
    expect(configuration.getByText('En cours')).toBeTruthy();
    // Aucune option « Ignorer » (statut SKIPPED supprimé en V1).
    expect(screen.queryByRole('button', { name: /ignorer/i })).toBeNull();
    // Action bloquée par l'abonnement : raison expliquée, pas de bouton.
    const catalogue = within(screen.getByTestId('step-catalogue'));
    expect(catalogue.queryByRole('button')).toBeNull();
    expect(catalogue.getByText("Disponible après l'activation de votre abonnement.")).toBeTruthy();
    // Prochaine étape mise en avant.
    const next = within(screen.getByTestId('onboarding-next'));
    expect(next.getByText('Votre premier site')).toBeTruthy();
  });

  it("démarre l'étape puis ouvre l'écran concerné (jamais de déclaration « terminée »)", async () => {
    render();
    const next = within(await screen.findByTestId('onboarding-next'));
    fireEvent.click(next.getByRole('button', { name: 'Créer mon premier site' }));
    expect((await screen.findByTestId('where')).textContent).toBe('/organization/sites?create=1');
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true),
    );
    const [url, init] = fetchMock.mock.calls.find(([, i]) => i?.method === 'PATCH')!;
    expect(String(url)).toContain('/onboarding/steps/first_site');
    expect(JSON.parse(String(init?.body))).toEqual({ status: 'IN_PROGRESS' });
  });

  it('sans droit de gestion : navigation seulement, aucune écriture', async () => {
    render(['organization.onboarding.view']);
    fireEvent.click(
      within(await screen.findByTestId('step-users')).getByRole('button', {
        name: 'Ajouter un utilisateur',
      }),
    );
    expect((await screen.findByTestId('where')).textContent).toBe('/users/members?create=1');
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
  });

  it("terminée mais abonnement en attente : l'activation reste distincte", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse({
        ...ONBOARDING,
        completed: true,
        status: 'COMPLETED',
        current_step: 'catalogue',
      }),
    );
    render();
    expect((await screen.findByTestId('onboarding-completed')).textContent).toContain(
      "attente d'activation",
    );
    expect(
      within(screen.getByTestId('onboarding-next')).getByText('Recommandation :'),
    ).toBeTruthy();
  });
});
