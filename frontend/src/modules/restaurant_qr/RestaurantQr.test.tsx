// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { Toast } from 'primereact/toast';
import type { ReactNode, RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import OrdersPage from '@/modules/restaurant_orders/OrdersPage';
import { SITES, jsonResponse, pageOf, renderWithCapabilities } from '@/shared/testing';
import { ToastContext } from '@/shared/ui/toast';

import PointsPage from './PointsPage';
import { PublicQrApp } from './public/PublicQrApp';
import { PublicMenuPage } from './public/PublicMenuPage';
import { PublicTrackingPage } from './public/PublicTrackingPage';
import { applyPublicPagePolicies, PUBLIC_CSP, takeFragmentSecret } from './public/secret';

/** Palier R7 (ADR-0052) : pages publiques, administration des points QR, file « À confirmer ». */

const TOKEN = 'A'.repeat(43);
const SECRET = 'b'.repeat(43);
const ONE_SITE = [SITES[0]!];
const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

const fetchMock = vi.fn<typeof fetch>();
const show = vi.fn();
const withToast = (node: ReactNode) => {
  const toast = { current: { show } as unknown as Toast } as RefObject<Toast | null>;
  return <ToastContext.Provider value={toast}>{node}</ToastContext.Provider>;
};
const requests = (method = 'GET') =>
  fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? 'GET') === method)
    .map(([url, init]) => ({
      url: String(url),
      init: init ?? {},
      body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null,
    }));

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  show.mockReset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  try {
    sessionStorage.clear();
  } catch {
    // stockage indisponible
  }
  window.history.replaceState(null, '', '/');
});

const menu = (over: Record<string, unknown> = {}) => ({
  establishment: 'Maquis Le Baobab',
  site_name: 'Centre',
  point: { kind: 'TABLE', label: 'T4' },
  currency: 'XOF',
  sections: [
    {
      id: 'sec1',
      name: 'Boissons',
      items: [
        {
          id: 'i1',
          name: 'Bière',
          packaging_name: null,
          description: 'Bien fraîche',
          price: '700.00',
          available: true,
        },
        {
          id: 'i2',
          name: 'Jus',
          packaging_name: null,
          description: null,
          price: '500.00',
          available: false,
        },
      ],
    },
  ],
  ordering: {
    max_lines: 30,
    max_quantity_per_line: 3,
    customer_name_mode: 'REQUIRED',
    customer_phone_mode: 'HIDDEN',
  },
  ...over,
});

describe('secret du fragment et politiques de la page publique', () => {
  it('lit le jeton du fragment, le garde pour l’onglet et l’efface de l’adresse', () => {
    window.history.replaceState(null, '', `/q#${TOKEN}`);
    expect(takeFragmentSecret('k')).toBe(TOKEN);
    expect(window.location.hash).toBe('');
    expect(window.location.pathname).toBe('/q');
    // Rechargement de l'onglet : relu depuis l'onglet (jamais depuis l'adresse).
    expect(takeFragmentSecret('k')).toBe(TOKEN);
  });

  it('refuse un fragment malformé et l’efface aussi', () => {
    window.history.replaceState(null, '', '/q#pas-un-jeton');
    expect(takeFragmentSecret('other')).toBeNull();
    expect(window.location.hash).toBe('');
  });

  it('pose une CSP sans ressource tierce et aucun référent', () => {
    applyPublicPagePolicies();
    const csp = document.querySelector('meta[http-equiv="Content-Security-Policy"]');
    expect(csp?.getAttribute('content')).toBe(PUBLIC_CSP);
    expect(PUBLIC_CSP).toContain("default-src 'self'");
    expect(PUBLIC_CSP).not.toMatch(/https?:/);
    expect(document.querySelector('meta[name="referrer"]')?.getAttribute('content')).toBe(
      'no-referrer',
    );
  });
});

describe('nouveau QR scanné dans le même onglet', () => {
  it('remplace le jeton (seul le fragment change), l’efface de l’adresse, repart du nouveau point', async () => {
    const OTHER = 'C'.repeat(43);
    fetchMock.mockImplementation(async () => jsonResponse(menu()));
    window.history.replaceState(null, '', `/q#${TOKEN}`);
    render(<PublicQrApp />);
    expect(await screen.findByRole('heading', { name: 'Maquis Le Baobab' })).toBeTruthy();
    expect(window.location.hash).toBe('');
    window.history.replaceState(null, '', `/q#${OTHER}`);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    await waitFor(() => expect(requests().length).toBe(2));
    const tokens = requests().map((r) => (r.init.headers as Record<string, string>)['X-QR-Token']);
    expect(tokens).toEqual([TOKEN, OTHER]);
    expect(window.location.hash).toBe('');
  });
});

describe('menu public et commande (R7-B / R7-C)', () => {
  it('affiche le menu, envoie le jeton en en-tête et jamais de session', async () => {
    fetchMock.mockResolvedValue(jsonResponse(menu()));
    render(<PublicMenuPage token={TOKEN} />);
    expect(await screen.findByRole('heading', { name: 'Maquis Le Baobab' })).toBeTruthy();
    expect(screen.getByText('Centre · Table T4')).toBeTruthy();
    expect(screen.getByText('Épuisé')).toBeTruthy();
    const [first] = requests();
    expect(first!.url).toBe('/api/v1/public/restaurant/qr/menu');
    const headers = first!.init.headers as Record<string, string>;
    expect(headers['X-QR-Token']).toBe(TOKEN);
    expect(headers.Authorization).toBeUndefined();
    expect(first!.init.credentials).toBe('omit');
    expect(first!.url).not.toContain(TOKEN);
  });

  it('commande sans compte : quantités bornées, nom exigé, même clé si renvoyée', async () => {
    const assign = vi.fn();
    vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...window.location,
      assign,
    } as unknown as Location);
    let attempt = 0;
    fetchMock.mockImplementation(async (_url, init) => {
      if ((init?.method ?? 'GET') === 'GET') return jsonResponse(menu());
      attempt += 1;
      if (attempt === 1) return jsonResponse({ code: 'rate_limited', retry_after: 5 }, 429);
      return jsonResponse(
        {
          daily_number: 12,
          status: 'PENDING_CONFIRMATION',
          tracking_url: `https://app.example.test/q/track#${SECRET}`,
          replayed: false,
        },
        201,
      );
    });
    render(<PublicMenuPage token={TOKEN} />);
    const more = await screen.findByRole('button', { name: 'Ajouter un Bière' });
    for (let i = 0; i < 5; i += 1) fireEvent.click(more);
    expect(text(screen.getByRole('region', { name: 'Votre commande' }))).toContain('3 × Bière');
    expect(more.hasAttribute('disabled')).toBe(true); // plafond du site
    const send = screen.getByRole('button', { name: 'Envoyer la commande' });
    expect(send.hasAttribute('disabled')).toBe(true); // nom obligatoire
    fireEvent.change(screen.getByLabelText('Votre nom *'), { target: { value: 'Awa' } });
    fireEvent.click(send);
    // Premier envoi refusé (limitation) : le contenu ne change pas, le renvoi garde la clé.
    await waitFor(() => expect(requests('POST')).toHaveLength(1));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Envoyer la commande' }).hasAttribute('disabled'),
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Envoyer la commande' }));
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(`https://app.example.test/q/track#${SECRET}`),
    );
    const [firstPost, secondPost] = requests('POST');
    expect(firstPost!.body!.client_key).toBe(secondPost!.body!.client_key);
    expect(String(firstPost!.body!.client_key)).toMatch(/^[0-9a-f-]{8}-[0-9a-f-]{4}-4/);
    expect(firstPost!.body).toEqual({
      client_key: firstPost!.body!.client_key,
      lines: [{ menu_item_id: 'i1', quantity: 3, note: null }],
      customer_name: 'Awa',
      customer_phone: null,
    });
  });

  it('HTTP sur le réseau local (sans crypto.randomUUID) : l’envoi part, raison du bouton désactivé écrite', async () => {
    // Téléphone sur http://<IP du serveur> : contexte NON sécurisé, ``randomUUID`` absent ;
    // ``getRandomValues`` reste disponible (même aléa cryptographique).
    const real = globalThis.crypto;
    vi.stubGlobal('crypto', {
      getRandomValues: <T extends ArrayBufferView>(array: T) =>
        real.getRandomValues(array as T & ArrayBufferView<ArrayBuffer>),
    });
    const assign = vi.fn();
    vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...window.location,
      assign,
    } as unknown as Location);
    fetchMock.mockImplementation(async (_url, init) => {
      if ((init?.method ?? 'GET') === 'GET') return jsonResponse(menu());
      return jsonResponse(
        {
          daily_number: 3,
          status: 'PENDING_CONFIRMATION',
          tracking_url: `http://192.168.1.20:5173/q/track#${SECRET}`,
          replayed: false,
        },
        201,
      );
    });
    render(<PublicMenuPage token={TOKEN} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter un Bière' }));
    const send = screen.getByRole('button', { name: 'Envoyer la commande' });
    // Nom obligatoire non saisi : bouton désactivé ET raison affichée (jamais un bouton muet).
    expect(send.hasAttribute('disabled')).toBe(true);
    expect(screen.getByText('Indiquez votre nom pour envoyer la commande.')).toBeTruthy();
    expect(send.getAttribute('aria-describedby')).toBe('qr-send-hint');
    fireEvent.change(screen.getByLabelText('Votre nom *'), { target: { value: 'Awa' } });
    expect(screen.queryByText('Indiquez votre nom pour envoyer la commande.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Envoyer la commande' }));
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(`http://192.168.1.20:5173/q/track#${SECRET}`),
    );
    const [post] = requests('POST');
    expect(String(post!.body!.client_key)).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });

  it('signale un article devenu indisponible sans motif interne', async () => {
    fetchMock.mockImplementation(async (_url, init) => {
      if ((init?.method ?? 'GET') === 'GET') {
        return jsonResponse(
          menu({ ordering: { ...menu().ordering, customer_name_mode: 'HIDDEN' } }),
        );
      }
      return jsonResponse({ code: 'qr_order_item_unavailable', items: ['i1'] }, 422);
    });
    render(<PublicMenuPage token={TOKEN} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Ajouter un Bière' }));
    fireEvent.click(screen.getByRole('button', { name: 'Envoyer la commande' }));
    expect(await screen.findByText(/ne sont plus disponibles/)).toBeTruthy();
  });

  it('QR indisponible : message générique', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ code: 'qr_unavailable' }, 404));
    render(<PublicMenuPage token={TOKEN} />);
    expect(await screen.findByText(/QR Code n'est pas disponible/)).toBeTruthy();
  });
});

describe('suivi public (R7-E)', () => {
  it('numéro, état, reste à régler et motif d’un refus', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        establishment: 'Maquis Le Baobab',
        site_name: 'Centre',
        point: { kind: 'COUNTER', label: 'Comptoir' },
        currency: 'XOF',
        daily_number: 12,
        status: 'REJECTED',
        prep_status: 'NONE',
        lines: [{ name: 'Bière', packaging_name: null, quantity: '2.000', status: 'CANCELLED' }],
        total: '0.00',
        remaining_due: '0.00',
        rejection_reason: 'Plus de bière',
        available_until: '2026-11-02T00:00:00Z',
      }),
    );
    render(<PublicTrackingPage secret={SECRET} />);
    expect((await screen.findByTestId('qr-order-number')).textContent).toBe('12');
    expect(screen.getByTestId('qr-order-status').textContent).toBe('Refusée');
    expect(screen.getByText('Motif : Plus de bière')).toBeTruthy();
    const headers = requests()[0]!.init.headers as Record<string, string>;
    expect(headers['X-QR-Tracking']).toBe(SECRET);
    expect(requests()[0]!.url).toBe('/api/v1/public/restaurant/qr/track');
  });

  it('lien expiré : message générique', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ code: 'qr_tracking_unavailable' }, 404));
    render(<PublicTrackingPage secret={SECRET} />);
    expect(await screen.findByText(/lien de suivi n'est plus disponible/)).toBeTruthy();
  });
});

const point = (over: Record<string, unknown> = {}) => ({
  id: 'p1',
  site_id: 's1',
  site_name: 'Boutique',
  kind: 'COUNTER',
  table_id: null,
  table_label: null,
  table_active: null,
  zone_name: null,
  label: 'Comptoir',
  display_name: 'Comptoir',
  is_active: true,
  token_generated_at: '2026-11-01T10:00:00Z',
  token_generated_by_name: 'Moi',
  created_at: '2026-11-01T10:00:00Z',
  updated_at: '2026-11-01T10:00:00Z',
  ...over,
});

describe('administration des points QR (R7-A)', () => {
  it('émission : QR affiché une seule fois, image du serveur, aucun jeton relu', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if ((init?.method ?? 'GET') === 'POST') {
        return jsonResponse(
          {
            point: point(),
            url: `https://app.example.test/q#${TOKEN}`,
            svg: '<svg viewBox="0 0 41 41"></svg>',
          },
          201,
        );
      }
      if (String(url).startsWith('/api/v1/restaurant/tables')) return pageOf([]);
      return pageOf([]);
    });
    renderWithCapabilities(withToast(<PointsPage />), {
      permissions: ['restaurant.qr.point.view', 'restaurant.qr.point.manage'],
      sites: ONE_SITE,
      absentModules: ['restaurant.tables'],
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Nouveau QR Code' }));
    const dialog = await screen.findByRole('dialog', { name: 'Nouveau QR Code' });
    fireEvent.change(within(dialog).getByLabelText(/Nom du point/), {
      target: { value: 'Comptoir' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Générer le QR Code' }));
    const issued = await screen.findByRole('dialog', { name: 'QR Code — Comptoir' });
    expect(text(issued)).toContain('il ne pourra plus être affiché');
    const image = within(issued).getAllByRole('img', { name: 'QR Code à scanner' })[0]!;
    expect(image.getAttribute('src')).toMatch(/^data:image\/svg\+xml/);
    expect(text(issued)).not.toContain(TOKEN); // le lien n'est pas affiché en clair
    expect(requests('POST')[0]!.body).toEqual({
      site_id: 's1',
      kind: 'COUNTER',
      label: 'Comptoir',
    });
  });

  it('régénération : confirmation explicite de l’invalidation des QR imprimés', async () => {
    fetchMock.mockResolvedValue(pageOf([point()]));
    renderWithCapabilities(withToast(<PointsPage />), {
      permissions: ['restaurant.qr.point.view', 'restaurant.qr.point.manage'],
      sites: ONE_SITE,
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Régénérer' }));
    expect(
      await screen.findByText(
        /anciens QR Codes imprimés de ce point deviendront immédiatement invalides/,
      ),
    ).toBeTruthy();
    expect(requests('POST')).toHaveLength(0); // rien sans confirmation
  });
});

const pending = (over: Record<string, unknown> = {}) => ({
  id: 'o1',
  site_id: 's1',
  site_name: 'Boutique',
  business_date: '2026-11-01',
  daily_number: 7,
  channel: 'QR',
  service_mode: 'COUNTER',
  customer_id: null,
  customer_name: null,
  call_name: 'Awa',
  contact_phone: null,
  table_id: null,
  table: null,
  status: 'PENDING_CONFIRMATION',
  prep_status: 'RECEIVED',
  settlement_status: 'UNSETTLED',
  payment_timing: 'AT_END',
  assigned_user_id: null,
  assigned_name: null,
  assigned_at: null,
  priority: 'NORMAL',
  total: '1400.00',
  line_counts: { received: 1, in_preparation: 0, ready: 0, served: 0, cancelled: 0 },
  lines: [],
  sale_id: null,
  sale_number: null,
  payment_status: null,
  created_at: '2026-11-01T10:00:00Z',
  updated_at: '2026-11-01T10:00:00Z',
  ...over,
});

describe('commandes à confirmer (R7-D)', () => {
  it('onglet « À confirmer » : confirmer ou refuser avec un motif obligatoire', async () => {
    fetchMock.mockImplementation(async (url, init) => {
      if ((init?.method ?? 'GET') === 'POST') return jsonResponse(pending({ status: 'REJECTED' }));
      return pageOf(String(url).includes('state=pending') ? [pending()] : []);
    });
    renderWithCapabilities(withToast(<OrdersPage />), {
      permissions: ['restaurant.orders.order.view', 'restaurant.orders.order.confirm'],
      sites: ONE_SITE,
    });
    // Les colonnes de suivi ne lisent que les commandes OUVERTES.
    await waitFor(() => expect(requests().some((r) => r.url.includes('state=open'))).toBe(true));
    fireEvent.click(screen.getByRole('tab', { name: 'À confirmer' }));
    const list = await screen.findByRole('list', {
      name: 'Commandes reçues par QR Code à confirmer',
    });
    expect(text(list)).toContain('n° 7');
    fireEvent.click(within(list).getByRole('button', { name: 'Refuser la commande n° 7' }));
    const dialog = await screen.findByRole('dialog', { name: 'Refuser la commande n° 7' });
    const reject = within(dialog).getByRole('button', { name: 'Refuser' });
    expect(reject.hasAttribute('disabled')).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/Motif/), {
      target: { value: 'Cuisine fermée' },
    });
    fireEvent.click(reject);
    await waitFor(() =>
      expect(requests('POST')).toEqual([
        expect.objectContaining({
          url: '/api/v1/restaurant/orders/o1/reject',
          body: { reason: 'Cuisine fermée' },
        }),
      ]),
    );
  });

  it('sans la permission : aucun bouton de décision', async () => {
    fetchMock.mockImplementation(async (url) =>
      pageOf(String(url).includes('state=pending') ? [pending()] : []),
    );
    renderWithCapabilities(withToast(<OrdersPage />), {
      permissions: ['restaurant.orders.order.view'],
      sites: ONE_SITE,
    });
    fireEvent.click(await screen.findByRole('tab', { name: 'À confirmer' }));
    await screen.findByRole('list', { name: 'Commandes reçues par QR Code à confirmer' });
    expect(screen.queryByRole('button', { name: /Confirmer la commande/ })).toBeNull();
  });
});
