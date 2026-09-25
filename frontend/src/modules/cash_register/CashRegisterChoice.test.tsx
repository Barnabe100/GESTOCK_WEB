// @vitest-environment jsdom
import { cleanup, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { pageOf, renderWithCapabilities } from '@/shared/testing';

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

  const render = (permissions = SELLER) =>
    renderWithCapabilities(<CashRegisterChoice siteId="s1" value={null} onChange={onChange} />, {
      permissions,
    });

  it('aucune caisse ouverte sur le site : avertissement', async () => {
    fetchMock.mockImplementation(async () => pageOf([]));
    render();
    expect(await screen.findByText(/Aucune caisse ouverte sur le site de la vente/)).toBeTruthy();
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain('status=OPEN');
    expect(url).toContain('site_id=s1');
  });

  it('une seule caisse ouverte : indiquée et retenue', async () => {
    fetchMock.mockImplementation(async () => pageOf([cashSession()]));
    render();
    expect(await screen.findByText(/Encaissé dans : Caisse principale/)).toBeTruthy();
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('r1'));
  });

  it('plusieurs caisses ouvertes : choix obligatoire', async () => {
    fetchMock.mockImplementation(async () =>
      pageOf([
        cashSession(),
        cashSession({ id: 'cs2', cash_register_id: 'r2', cash_register_name: 'Caisse 2' }),
      ]),
    );
    render();
    expect(await screen.findByLabelText(/^Caisse/)).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('sans droit de consultation des sessions : rien (le serveur choisit ou refuse)', () => {
    render([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
