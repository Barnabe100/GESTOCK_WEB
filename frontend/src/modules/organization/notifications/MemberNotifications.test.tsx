// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { subscriptionModule } from '@/modules/subscription';
import { jsonResponse, renderWithCapabilities } from '@/shared/testing';

import { organizationModule } from '..';
import type { MemberNotification } from './api';
import MemberNotificationsPanel from './MemberNotificationsPanel';
import { NotificationsBell } from './NotificationsBell';
import NotificationsCenterPage from './NotificationsCenterPage';

const note = (over: Partial<MemberNotification>): MemberNotification => ({
  id: 'm-1',
  kind: 'restaurant.order.ready',
  site: { id: 'site-1', name: 'Maquis Centre', code: 'MAQ' },
  entity_type: 'restaurant_order',
  entity_id: 'order-12',
  payload: { daily_number: 12, line_count: 2, business_date: '2026-10-11' },
  created_at: '2026-10-11T12:00:00Z',
  read_at: null,
  resolved: false,
  ...over,
});

const NOTES = [
  note({}),
  note({
    id: 'm-2',
    entity_id: 'order-7',
    payload: { daily_number: 7, line_count: 1 },
    read_at: '2026-10-11T11:00:00Z',
    resolved: true,
  }),
  note({ id: 'm-3', kind: 'autre.type', entity_type: 'autre', resolved: null }),
];

const fetchMock = vi.fn<typeof fetch>();
const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
const called = (part: string) => fetchMock.mock.calls.some(([u]) => String(u).includes(part));

function api() {
  fetchMock.mockImplementation(async (url, init) => {
    const u = String(url);
    if (init?.method === 'POST') return new Response(null, { status: 204 });
    if (u.includes('/member-notifications/unread-count')) return jsonResponse({ unread: 3 });
    if (u.includes('/notifications/unread-count')) return jsonResponse({ unread: 4 });
    if (u.includes('/member-notifications?')) {
      const unread = u.includes('unread=true');
      const items = unread ? NOTES.filter((n) => n.read_at === null) : NOTES;
      return jsonResponse({ items, total: items.length, limit: 25, offset: 0 });
    }
    if (u.includes('/notifications?')) {
      return jsonResponse({ items: [], total: 0, limit: 25, offset: 0 });
    }
    return jsonResponse({ code: 'not_found' }, 404);
  });
}

const MEMBER = 'organization.notification.view';
const SUBSCRIPTION = 'subscription.subscription.view';
const MODULES = [
  { code: 'organization', status: 'available' },
  { code: 'subscription', status: 'available' },
] as never;
const REGISTRY = [organizationModule, subscriptionModule];

describe('Centre de notifications unique (R4, ADR-0049 D8)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    api();
  });
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('indicateur unique : somme des non lues des sources autorisées', async () => {
    renderWithCapabilities(<NotificationsBell modules={REGISTRY} />, {
      permissions: [MEMBER, SUBSCRIPTION],
      modules: MODULES,
    });
    await waitFor(() => expect(screen.getByTestId('unread-count').textContent).toBe('7'));
    expect(screen.getByRole('button', { name: 'Notifications : 7 non lues' })).toBeTruthy();
    expect(screen.getAllByTestId('notifications-bell')).toHaveLength(1);
  });

  it('indicateur : seules les sources autorisées sont interrogées ; aucune → rien', async () => {
    renderWithCapabilities(<NotificationsBell modules={REGISTRY} />, {
      permissions: [MEMBER],
      modules: MODULES,
    });
    expect((await screen.findByTestId('unread-count')).textContent).toBe('3');
    expect(called('/member-notifications/unread-count')).toBe(true);
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).endsWith('/notifications/unread-count')),
    ).toBe(false);
    cleanup();
    fetchMock.mockClear();
    renderWithCapabilities(<NotificationsBell modules={REGISTRY} />, {
      permissions: [],
      modules: MODULES,
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId('notifications-bell')).toBeNull();
  });

  it('interrogation périodique des notifications des membres : 15 s au plus', () => {
    const source = organizationModule.notifications?.find((s) => s.key === 'activity');
    expect(source?.permission).toBe(MEMBER);
    expect(source?.unreadPath).toBe('/member-notifications/unread-count');
    expect(source!.refetchInterval).toBeGreaterThanOrEqual(10_000);
    expect(source!.refetchInterval).toBeLessThanOrEqual(15_000);
  });

  it('liste : texte, « traitée » calculée par le serveur, lu / non lu, type inconnu', async () => {
    renderWithCapabilities(<MemberNotificationsPanel />, {
      permissions: [MEMBER, 'restaurant.orders.order.view'],
    });
    const rows = await screen.findAllByTestId('member-notification');
    expect(rows[0]!.textContent).toContain(
      'Commande n° 12 prête : 2 articles à servir (Maquis Centre)',
    );
    expect(rows[0]!.textContent).toContain('À traiter');
    expect(rows[1]!.textContent).toContain('Commande n° 7 prête : 1 article à servir');
    expect(rows[1]!.textContent).toContain('Traitée');
    expect(rows[2]!.textContent).toContain('Notification — site Maquis Centre');
    expect(rows[2]!.textContent).not.toMatch(/Traitée|À traiter/);
    expect(screen.getAllByText('Lu')).toHaveLength(1);
    expect(document.body.textContent).not.toMatch(/memberNotifications\.|notifications\./);
  });

  it('marquer comme lu, tout marquer, filtre « Non lues » transmis au serveur', async () => {
    renderWithCapabilities(<MemberNotificationsPanel />, { permissions: [MEMBER] });
    await screen.findAllByTestId('member-notification');
    fireEvent.click(screen.getByText('Non lues'));
    await waitFor(() => expect(called('unread=true')).toBe(true));
    const first = (await screen.findAllByTestId('member-notification'))[0]!.closest(
      'tr',
    ) as HTMLElement;
    fireEvent.click(within(first).getByRole('button', { name: 'Marquer comme lu' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]![0]).toMatch(/\/member-notifications\/m-1\/read$/);
    fireEvent.click(screen.getByRole('button', { name: 'Tout marquer comme lu' }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1]![0]).toMatch(/\/member-notifications\/read-all$/);
  });

  it('« Ouvrir » : seulement avec la lecture des commandes ; marque lu et ouvre la commande', async () => {
    renderWithCapabilities(<MemberNotificationsPanel />, { permissions: [MEMBER] });
    await screen.findAllByTestId('member-notification');
    expect(screen.queryByRole('button', { name: 'Ouvrir' })).toBeNull();
    cleanup();
    renderWithCapabilities(<MemberNotificationsPanel />, {
      permissions: [MEMBER, 'restaurant.orders.order.view'],
      extraRoutes: [{ path: '/restaurant/orders/:id', element: <p>Fiche commande</p> }],
    });
    await screen.findAllByTestId('member-notification');
    expect(screen.getAllByRole('button', { name: 'Ouvrir' })).toHaveLength(2);
    fireEvent.click(screen.getAllByRole('button', { name: 'Ouvrir' })[0]!);
    expect(await screen.findByText('Fiche commande')).toBeTruthy();
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]![0]).toMatch(/\/member-notifications\/m-1\/read$/);
  });

  it('page : une section par source autorisée ; aucune → message', async () => {
    renderWithCapabilities(<NotificationsCenterPage modules={REGISTRY} />, {
      permissions: [MEMBER, SUBSCRIPTION],
      modules: MODULES,
    });
    expect(await screen.findByTestId('notifications-activity')).toBeTruthy();
    expect(await screen.findByTestId('notifications-subscription')).toBeTruthy();
    expect(screen.getByText('Activité')).toBeTruthy();
    expect(screen.getByText("Rappels d'échéance des licences")).toBeTruthy();
    cleanup();
    renderWithCapabilities(<NotificationsCenterPage modules={REGISTRY} />, {
      permissions: [MEMBER],
      modules: MODULES,
    });
    expect(await screen.findByTestId('notifications-activity')).toBeTruthy();
    expect(screen.queryByTestId('notifications-subscription')).toBeNull();
    cleanup();
    renderWithCapabilities(<NotificationsCenterPage modules={REGISTRY} />, {
      permissions: [],
      modules: MODULES,
    });
    expect(await screen.findByText('Aucune notification.')).toBeTruthy();
  });
});
