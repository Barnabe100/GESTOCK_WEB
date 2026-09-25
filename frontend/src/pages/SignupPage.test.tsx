// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/core/api/client';
import type { SignupInput } from '@/core/api/types';
import { AuthContext, type AuthContextValue } from '@/core/auth/AuthContext';
import { jsonResponse } from '@/shared/testing';

import { SignupPage } from './SignupPage';

const COUNTRIES = [
  {
    code: 'BF',
    name: 'Burkina Faso',
    currency: 'XOF',
    calling_code: 226,
    timezone: 'Africa/Ouagadougou',
  },
  { code: 'FR', name: 'France', currency: 'EUR', calling_code: 33, timezone: 'Europe/Paris' },
];
const PROFILES = {
  sectors: [
    { code: 'retail', name: 'Commerce', icon: 'pi pi-shopping-bag' },
    { code: 'restaurant', name: 'Restauration', icon: 'pi pi-shop' },
  ],
  profiles: [
    { code: 'retail.alimentation', name: 'Alimentation', description: null, sector: 'retail' },
    { code: 'restaurant.maquis', name: 'Maquis', description: null, sector: 'restaurant' },
  ],
};
const PLAN = {
  description: null,
  contact_required: false,
  self_service: true,
  trial_days: 0,
  price_displayed: true,
  currency: 'XOF',
  limits: { max_sites: 1, max_users: 5 },
  modules: ['pos'],
};
const PLANS = {
  contact_email: 'ventes@technova.example',
  plans: [
    {
      ...PLAN,
      code: 'STANDARD',
      name: 'Standard',
      periods: [
        { billing_period: 'monthly', price: '5000.00' },
        { billing_period: 'annual', price: '50000.00' },
      ],
    },
    {
      ...PLAN,
      code: 'SANSPRIX',
      name: 'Discret',
      price_displayed: false,
      currency: null,
      trial_days: 14,
      periods: [{ billing_period: 'monthly', price: null }],
    },
    {
      ...PLAN,
      code: 'ENTREPRISE',
      name: 'Entreprise',
      contact_required: true,
      self_service: false,
      price_displayed: false,
      currency: null,
      periods: [],
      limits: { max_sites: null, max_users: null },
    },
  ],
};

function renderSignup(
  signup = vi.fn<AuthContextValue['signup']>(),
  status: AuthContextValue['status'] = 'anonymous',
) {
  const auth = {
    status,
    user: null,
    tenantId: null,
    memberships: [],
    login: vi.fn(),
    signup,
    logout: vi.fn(),
    selectTenant: vi.fn(),
    changePassword: vi.fn(),
  } as unknown as AuthContextValue;
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <AuthContext.Provider value={auth}>
        <MemoryRouter initialEntries={['/signup']}>
          <Routes>
            <Route path="/signup" element={<SignupPage />} />
            <Route path="/" element={<p>Accueil</p>} />
            <Route path="/login" element={<p>Connexion</p>} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
  return signup;
}

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label, { exact: false }), { target: { value } });
const next = () => fireEvent.click(screen.getByRole('button', { name: 'Suivant' }));

async function choose(inputId: string, option: string) {
  fireEvent.click(document.querySelector(`#${inputId}`)?.closest('.p-dropdown') as Element);
  // Panneau animé : options accessibles avec `hidden: true` en jsdom.
  fireEvent.click(
    await screen.findByRole('option', { name: option, hidden: true }, { timeout: 3000 }),
  );
}

async function fillAccount() {
  await screen.findByLabelText('Nom complet', { exact: false });
  type('Nom complet', 'Awa Traoré');
  type('Adresse email', 'awa@example.com');
  fireEvent.change(document.querySelector('#password') as Element, {
    target: { value: 'Motdepasse-1' },
  });
  fireEvent.change(document.querySelector('#confirm') as Element, {
    target: { value: 'Motdepasse-1' },
  });
  next();
  await screen.findByText('Informations recommandées');
}

describe('inscription publique', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/public/geo/countries')) return jsonResponse(COUNTRIES);
      if (u.includes('/public/business-profiles')) return jsonResponse(PROFILES);
      if (u.includes('/public/plans')) return jsonResponse(PLANS);
      return jsonResponse({}, 404);
    });
  });

  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('valide chaque étape avant de continuer', async () => {
    renderSignup();
    await screen.findByLabelText('Nom complet', { exact: false });
    next();
    expect(await screen.findAllByRole('alert')).not.toHaveLength(0);
    expect(screen.getByText('Étape 1 sur 5')).toBeTruthy();

    type('Nom complet', 'Awa');
    type('Adresse email', 'awa@example.com');
    fireEvent.change(document.querySelector('#password') as Element, {
      target: { value: 'Motdepasse-1' },
    });
    fireEvent.change(document.querySelector('#confirm') as Element, {
      target: { value: 'Autre-mot-2' },
    });
    next();
    expect(await screen.findByText('Les mots de passe ne correspondent pas')).toBeTruthy();
  });

  it('parcours complet : pays → devise, activité, offre publiée, envoi sans champ imposé', async () => {
    const signup = renderSignup(vi.fn(async () => undefined));
    await fillAccount();

    // Entreprise : seuls nom, pays et devise sont obligatoires ; recommandés facultatifs.
    await screen.findByText('Informations recommandées');
    type("Nom de l'entreprise", 'Supérette Awa');
    await choose('country_code', 'France');
    expect(screen.getByText('EUR', { selector: '.p-dropdown-label' })).toBeTruthy();
    await choose('country_code', 'Burkina Faso');
    expect(screen.getByText('XOF', { selector: '.p-dropdown-label' })).toBeTruthy();
    type('Ville', 'Ouagadougou');
    next();

    // Activité : secteur puis profil du secteur.
    const sectors = await screen.findByRole('radiogroup', { name: "Secteur d'activité" });
    fireEvent.click(within(sectors).getByRole('radio', { name: 'Commerce de détail' }));
    await choose('business_profile', 'Alimentation / Supérette');
    next();

    // Offres : prix publiés, prix masqué, offre sur contact (aucun prix inventé).
    const offers = await screen.findByRole('radiogroup', { name: 'Offre' });
    const standard = within(offers).getByRole('region', { name: 'Standard' });
    expect(within(standard).getByText(/5\s000\sF\sCFA \/ mois/)).toBeTruthy();
    expect(within(standard).getByText(/50\s000\sF\sCFA \/ an/)).toBeTruthy();
    const discret = within(offers).getByRole('region', { name: 'Discret' });
    expect(within(discret).getByText('Tarification sur demande')).toBeTruthy();
    expect(within(discret).getByText('Essai gratuit de 14 jours')).toBeTruthy();
    const entreprise = within(offers).getByRole('region', { name: 'Entreprise' });
    const contact = within(entreprise).getByRole('link', { name: 'Contacter TechNova' });
    expect(contact.getAttribute('href')).toContain('mailto:ventes@technova.example');
    expect(within(entreprise).queryByRole('radio')).toBeNull();
    expect(within(entreprise).getByText('Sites illimités')).toBeTruthy();
    fireEvent.click(within(standard).getByRole('radio', { name: 'Choisir cette offre' }));
    next();

    // Confirmation : activation en attente (aucun essai configuré) ; rien n'est envoyé
    // avant le clic explicite sur « Créer mon entreprise ».
    const summary = await screen.findByTestId('signup-summary');
    expect(signup).not.toHaveBeenCalled();
    expect(summary.textContent).toContain('Supérette Awa');
    expect(summary.textContent).toContain('Alimentation / Supérette');
    expect(screen.getByText(/en attente d'activation/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Créer mon entreprise' }));

    await waitFor(() => expect(signup).toHaveBeenCalledTimes(1));
    const input = signup.mock.calls[0]?.[0] as SignupInput;
    expect(input).toEqual({
      account: { full_name: 'Awa Traoré', email: 'awa@example.com', password: 'Motdepasse-1' },
      company: expect.objectContaining({
        name: 'Supérette Awa',
        country_code: 'BF',
        currency: 'XOF',
        city: 'Ouagadougou',
        phone: undefined,
      }) as SignupInput['company'],
      business_profile: 'retail.alimentation',
      plan_code: 'STANDARD',
      billing_period: 'monthly',
    });
    expect(await screen.findByText('Accueil')).toBeTruthy();
  });

  it("affiche l'erreur générique du serveur et revient à l'étape concernée", async () => {
    const signup = vi
      .fn<AuthContextValue['signup']>()
      .mockRejectedValueOnce(new ApiError(422, 'signup_unavailable', 'x'))
      .mockRejectedValueOnce(new ApiError(422, 'plan_not_available', 'x'));
    renderSignup(signup);
    await fillAccount();
    type("Nom de l'entreprise", 'Supérette Awa');
    await choose('country_code', 'Burkina Faso');
    next();
    fireEvent.click(
      within(await screen.findByRole('radiogroup', { name: "Secteur d'activité" })).getByRole(
        'radio',
        { name: 'Restauration' },
      ),
    );
    await choose('business_profile', 'Maquis');
    next();
    fireEvent.click(
      within(await screen.findByRole('region', { name: 'Discret' })).getByRole('radio', {
        name: 'Choisir cette offre',
      }),
    );
    next();
    expect(await screen.findByText(/essai gratuit de 14 jours/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Créer mon entreprise' }));
    expect(await screen.findByText(/Si vous avez déjà un compte, connectez-vous/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Créer mon entreprise' }));
    expect(await screen.findByText(/pas disponible en ligne/)).toBeTruthy();
    expect(screen.getByText('Étape 4 sur 5')).toBeTruthy();
  });

  it('redirige un utilisateur déjà connecté', async () => {
    renderSignup(vi.fn(), 'authenticated');
    expect(await screen.findByText('Accueil')).toBeTruthy();
  });

  it('sans offre publiée : aucune offre inventée, contact commercial', async () => {
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/public/plans'))
        return jsonResponse({ contact_email: 'ventes@technova.example', plans: [] });
      if (u.includes('/public/geo/countries')) return jsonResponse(COUNTRIES);
      return jsonResponse(PROFILES);
    });
    renderSignup();
    await fillAccount();
    type("Nom de l'entreprise", 'X');
    await choose('country_code', 'Burkina Faso');
    next();
    fireEvent.click(
      within(await screen.findByRole('radiogroup', { name: "Secteur d'activité" })).getByRole(
        'radio',
        { name: 'Commerce de détail' },
      ),
    );
    await choose('business_profile', 'Alimentation / Supérette');
    next();
    expect(
      await screen.findByText("Aucune offre n'est disponible en ligne pour le moment."),
    ).toBeTruthy();
    expect(screen.getByText(/ventes@technova.example/)).toBeTruthy();
    next();
    expect(await screen.findByText('Choisissez une offre.')).toBeTruthy();
  });
});
