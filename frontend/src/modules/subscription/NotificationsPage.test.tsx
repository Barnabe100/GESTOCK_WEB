// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { jsonResponse, renderWithCapabilities } from '@/shared/testing';

import type { AppNotification } from './api';
import { NotificationsBell } from './notificationDisplay';
import NotificationsPage from './NotificationsPage';

const note = (over: Partial<AppNotification>): AppNotification => ({
  id: 'n-1',
  kind: 'subscription.expiry',
  step: 30,
  reference_date: '2026-10-31',
  site: { id: 'site-1', name: 'Boutique', code: 'BTQ' },
  subscription_id: 'sub-1',
  data: { days_left: 30 },
  created_at: '2026-10-01T08:00:00Z',
  read_at: null,
  ...over,
});

const NOTES = [
  note({ id: 'n-30' }),
  note({ id: 'n-1', step: 1, read_at: '2026-10-30T09:00:00Z' }),
  note({ id: 'n-0', step: 0 }),
  note({ id: 'n+1', step: -1 }),
  note({
    id: 'n-trial',
    step: 5,
    data: { trial: true },
    site: { id: 's2', name: 'Dépôt', code: 'DEP' },
  }),
];

const fetchMock = vi.fn<typeof fetch>();
const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');

function api() {
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (init?.method === 'POST') return new Response(null, { status: 204 });
    if (u.includes('/notifications/unread-count')) return jsonResponse({ unread: 4 });
    if (u.includes('/notifications?')) {
      const unread = u.includes('unread=true');
      const items = unread ? NOTES.filter((n) => n.read_at === null) : NOTES;
      return jsonResponse({ items, total: items.length, limit: 25, offset: 0 });
    }
    return jsonResponse({ code: 'not_found' }, 404);
  });
}

const VIEW = ['subscription.subscription.view'];

describe('Notifications : rappels d’échéance', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    api();
  });
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('textes J-n, J0, J+n et essai ; historique avec lues et non lues', async () => {
    renderWithCapabilities(<NotificationsPage />, { permissions: VIEW });
    const rows = await screen.findAllByTestId('notification');
    const texts = rows.map((r) => r.textContent ?? '');
    expect(texts[0]).toContain(
      'La licence du site Boutique expire dans 30 jours (le 31 oct. 2026).',
    );
    expect(texts[1]).toContain('expire dans 1 jour');
    expect(texts[2]).toContain("Votre licence expire aujourd'hui : site Boutique.");
    expect(texts[3]).toContain("n'est plus couvert depuis le 1 nov. 2026");
    expect(texts[4]).toContain("La période d'essai du site Dépôt se termine dans 5 jours");
    expect(screen.getAllByText('Lu')).toHaveLength(1);
    expect(document.body.textContent).not.toMatch(/notifications\./);
  });

  it('filtre « Non lues » transmis au serveur ; marquer comme lu, tout marquer', async () => {
    renderWithCapabilities(<NotificationsPage />, { permissions: VIEW });
    await screen.findAllByTestId('notification');
    fireEvent.click(screen.getByText('Non lues'));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes('unread=true'))).toBe(true),
    );
    const first = (await screen.findAllByTestId('notification'))[0]!.closest('tr') as HTMLElement;
    fireEvent.click(within(first).getByRole('button', { name: 'Marquer comme lu' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]![0]).toMatch(/\/notifications\/n-30\/read$/);
    fireEvent.click(screen.getByRole('button', { name: 'Tout marquer comme lu' }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1]![0]).toMatch(/\/notifications\/read-all$/);
  });

  it('indicateur : nombre de non lues', async () => {
    renderWithCapabilities(<NotificationsBell />, { permissions: VIEW });
    expect((await screen.findByTestId('unread-count')).textContent).toBe('4');
    expect(screen.getByRole('button', { name: 'Notifications : 4 non lues' })).toBeTruthy();
  });

  it('indicateur sans le droit : aucune requête', async () => {
    renderWithCapabilities(<NotificationsBell />, { permissions: [] });
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/notifications'))).toBe(false);
    expect(screen.queryByTestId('unread-count')).toBeNull();
  });
});
