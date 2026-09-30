// @vitest-environment jsdom
import { cleanup, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';

import { CashRegisterChoice } from './CashRegisterChoice';
import { cashSession, SELLER } from './testData';

describe('caisse d’un encaissement en espèces', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const onChange = vi.fn();

  beforeEach(() => vi.stubGlobal('fetch', fetchMock));
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    onChange.mockReset();
    vi.unstubAllGlobals();
  });

  /** Caisse du site s1 activée ou non ; `sessions` : sessions ouvertes renvoyées. */
  const serve = (sessions: ReturnType<typeof cashSession>[], enabled = true) =>
    fetchMock.mockImplementation(async (url) =>
      String(url).includes('/cash/sites')
        ? jsonResponse([
            { site_id: 's1', site_name: 'Boutique', site_code: 'BOU', enabled, open_sessions: 0 },
          ])
        : pageOf(sessions),
    );

  const render = (permissions = SELLER) =>
    renderWithCapabilities(<CashRegisterChoice siteId="s1" value={null} onChange={onChange} />, {
      permissions,
    });

  it('site sans caisse : rien à choisir, aucune session demandée', async () => {
    serve([], false);
    const { container } = render();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/cash/sites');
    expect(container.textContent).toBe('');
  });

  it('aucune session ouverte à son nom sur le site : avertissement', async () => {
    serve([]);
    render();
    expect(await screen.findByText(/Aucune session de caisse ouverte à votre nom/)).toBeTruthy();
    const url = String(fetchMock.mock.calls.find(([u]) => String(u).includes('/sessions'))?.[0]);
    expect(url).toContain('status=OPEN');
    expect(url).toContain('site_id=s1');
  });

  it('une seule caisse ouverte : indiquée et retenue', async () => {
    serve([cashSession()]);
    render();
    expect(await screen.findByText(/Encaissé dans : Caisse principale/)).toBeTruthy();
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('r1'));
  });

  it('plusieurs postes ouverts à son nom : choix obligatoire', async () => {
    serve([
      cashSession(),
      cashSession({ id: 'cs2', cash_register_id: 'r2', cash_register_name: 'Caisse 2' }),
    ]);
    render();
    expect(await screen.findByLabelText(/^Poste de caisse/)).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('sans droit de consultation des sessions : rien (le serveur choisit ou refuse)', () => {
    render([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
