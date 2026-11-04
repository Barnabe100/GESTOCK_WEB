// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '@/core/i18n';
import { jsonResponse } from '@/shared/testing';

import { itemType, utcInputToIso } from './backups/RestorePanel';
import { BackupsPage } from './pages/BackupsPage';
import type {
  BackupItem,
  BackupOverview,
  BackupPolicy,
  BackupRunDetail,
  RestoreDetail,
} from './types';

const OVERVIEW: BackupOverview = {
  checked_at: '2026-11-04T10:00:00Z',
  level: 'ok',
  schema_revision: '0063',
  agent: {
    last_seen_at: '2026-11-04T09:59:30Z',
    age_seconds: 30,
    agent: 'agent@db',
    last_error: null,
    level: 'ok',
    problem: null,
  },
  schedules: [
    {
      category: 'physical',
      enabled_frequencies: ['weekly'],
      next_run_at: '2026-11-08T01:15:00Z',
      last_success_at: null,
      last_status: null,
      max_age_seconds: 691200,
      problem: null,
      level: 'ok',
    },
  ],
  active_runs: [],
  active_restores: [],
  window: {
    known: true,
    restorable_from: '2026-11-01T01:20:00Z',
    restorable_to: '2026-11-04T09:55:00Z',
  },
};

const RUN: BackupRunDetail = {
  id: 'run-1',
  kind: 'logical',
  scope: 'platform',
  tenant_id: null,
  tenant_name: null,
  trigger: 'manual',
  frequency: null,
  status: 'running',
  attempt: 1,
  max_attempts: 3,
  retry_of: null,
  scheduled_for: null,
  not_before: '2026-11-04T10:00:00Z',
  requested_label: 'admin@technova.example',
  requested_at: '2026-11-04T10:00:00Z',
  started_at: '2026-11-04T10:00:30Z',
  finished_at: null,
  heartbeat_at: '2026-11-04T10:00:40Z',
  progress_percent: 50,
  progress_message: 'entreprise 2/4',
  error: null,
  restore_operation_id: null,
  items_total: 1,
  items_succeeded: 1,
  items_failed: 0,
  can_cancel: false,
  result: {},
  items: [],
};

const ITEM: BackupItem = {
  id: 'item-1',
  tenant_id: 'tenant-a',
  tenant_name: 'Entreprise alpha',
  status: 'succeeded',
  source: 'live',
  source_point: '2026-11-04T02:45:00Z',
  table_count: 64,
  row_count: 1200,
  size_bytes: 2097152,
  manifest_sha256: 'ab'.repeat(32),
  alembic_revision: '0063',
  verified: true,
  verified_at: '2026-11-04T02:46:00Z',
  expires_at: '2026-12-04T02:46:00Z',
  protected_until: null,
  error: null,
  created_at: '2026-11-04T02:45:00Z',
  finished_at: '2026-11-04T02:46:00Z',
  run_id: 'run-0',
  run_trigger: 'schedule',
  run_frequency: 'daily',
  restorable: true,
  restorable_reason: null,
};

const RESTORE: RestoreDetail = {
  id: 'op-1',
  scope: 'tenant',
  tenant_id: 'tenant-a',
  tenant_name: 'Entreprise alpha',
  mode: 'logical',
  source_item_id: 'item-1',
  target_time: null,
  rollback_of: null,
  status: 'verified',
  reason: 'Erreur de saisie massive',
  requested_label: 'autre@technova.example',
  requested_at: '2026-11-04T10:05:00Z',
  verified_at: '2026-11-04T10:12:00Z',
  approved_label: null,
  approved_at: null,
  approval_reason: null,
  decision_expires_at: '2026-11-05T10:12:00Z',
  applied_at: null,
  rollback_deadline: null,
  pre_restore_item_id: null,
  materialized_item_id: null,
  progress_percent: 100,
  progress_message: 'vérifiée : en attente d’approbation',
  finished_at: null,
  error: null,
  manual_application: false,
  actions: { approve: true, reject: true, cancel: true, rollback: false, approve_blocked: null },
  verification: {
    copy: {
      message: 'restauration vérifiée',
      checks: [
        { name: 'other_tenants_unchanged', ok: true, detail: '412 empreinte(s) : 0 différence(s)' },
        { name: 'restored_exact', ok: true, detail: 'identique' },
      ],
    },
  },
  source_item: ITEM,
};

const POLICY: BackupPolicy = {
  id: 'pol-1',
  category: 'physical',
  frequency: 'weekly',
  enabled: true,
  run_time: '01:15:00',
  weekday: 7,
  month_day: null,
  interval_hours: null,
  start_date: '2026-11-04',
  timezone: 'UTC',
  retention_days: 30,
  next_run_at: '2026-11-08T01:15:00Z',
  last_enqueued_at: null,
  updated_at: '2026-11-04T00:00:00Z',
  fields: ['run_time', 'weekday'],
  schedulable: true,
};

const page = <T,>(items: T[]) => ({ items, total: items.length, limit: 25, offset: 0 });
const fetchMock = vi.fn<typeof fetch>();
const calls: { url: string; method: string; body: unknown }[] = [];
let runDetail: BackupRunDetail = RUN;

beforeEach(() => {
  runDetail = RUN;
  calls.length = 0;
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    const path = url.split('/operations/backups')[1]?.split('?')[0] ?? '';
    if (url.includes('/tenants?'))
      return jsonResponse(page([{ id: 'tenant-a', name: 'Entreprise alpha' }]));
    if (path === '/overview') return jsonResponse(OVERVIEW);
    if (path === '') return jsonResponse({ code: 'service_unavailable' }, 503);
    if (path === '/runs' && method === 'POST')
      return jsonResponse({ ...RUN, status: 'queued' }, 201);
    if (path === '/runs/run-1') return jsonResponse(runDetail);
    if (path === '/runs') return jsonResponse(page([RUN]));
    if (path === '/items') return jsonResponse(page([ITEM]));
    if (path === '/restores' && method === 'POST')
      return jsonResponse({ ...RESTORE, status: 'requested' }, 201);
    if (path === '/restores') return jsonResponse(page([RESTORE]));
    if (path === '/restores/op-1') return jsonResponse(RESTORE);
    if (path === '/restores/op-1/approve') return jsonResponse({ ...RESTORE, status: 'approved' });
    if (path === '/policies') return jsonResponse([POLICY]);
    if (path.startsWith('/policies/'))
      return jsonResponse({ ...POLICY, ...body, next_run_at: '2026-11-11T21:30:00Z' });
    return jsonResponse({ code: 'not_found' }, 404);
  });
});
afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const renderAt = (tab?: string) =>
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter
        initialEntries={[tab ? `/tech-admin/backups?tab=${tab}` : '/tech-admin/backups']}
      >
        <BackupsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const fill = (testId: string, value: string) =>
  fireEvent.change(screen.getByTestId(testId), { target: { value } });

describe('console — Sauvegarder, Restaurer, Paramètres', () => {
  it('Sauvegarder : demande immédiate puis progression et résultat relus sur le serveur', async () => {
    renderAt();
    expect((await screen.findByTestId('agent-state')).textContent).toContain('Actif');
    fireEvent.click(screen.getByTestId('action-backup'));
    fireEvent.click(await screen.findByTestId('backup-now-start'));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/runs'))).toBe(true),
    );
    const post = calls.find((c) => c.method === 'POST')!;
    expect(post.body).toEqual({
      kind: 'logical',
      scope: 'platform',
      tenant_id: null,
      reason: null,
    });
    expect((await screen.findByTestId('backup-progress')).textContent).toContain('entreprise 2/4');
  });

  it('Sauvegarder : une erreur de l’agent est affichée', async () => {
    runDetail = { ...RUN, status: 'failed', error: 'espace insuffisant' };
    renderAt();
    fireEvent.click(await screen.findByTestId('action-backup'));
    fireEvent.click(await screen.findByTestId('backup-now-start'));
    expect((await screen.findByTestId('backup-error')).textContent).toContain('espace insuffisant');
  });

  it('Restaurer : sauvegarde choisie dans l’historique, raison obligatoire, demande envoyée', async () => {
    renderAt('restore');
    fireEvent.click(await screen.findByTestId('restore-item-item-1'));
    fireEvent.click(screen.getByTestId('reason-accept'));
    expect(screen.getByText(/Raison obligatoire/)).toBeTruthy();
    fill('decision-reason', 'Erreur de saisie massive');
    fireEvent.click(screen.getByTestId('reason-accept'));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/restores'))).toBe(true),
    );
    expect(calls.find((c) => c.method === 'POST')!.body).toEqual({
      scope: 'tenant',
      tenant_id: 'tenant-a',
      mode: 'logical',
      source_item_id: 'item-1',
      reason: 'Erreur de saisie massive',
    });
    // Le rapport de vérification s'affiche dans le détail ouvert.
    expect((await screen.findByTestId('report-copy')).textContent).toContain(
      'Autres entreprises inchangées',
    );
  });

  it('Restaurer : approbation par un second administrateur, confirmation explicite', async () => {
    renderAt('restore');
    fireEvent.click(await screen.findByText('autre@technova.example'));
    fireEvent.click(await screen.findByTestId('restore-approve'));
    fill('decision-reason', 'Rapport relu, conforme');
    expect((screen.getByTestId('reason-accept') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(document.getElementById('decision-confirm')!);
    fireEvent.click(screen.getByTestId('reason-accept'));
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith('/restores/op-1/approve'))).toBe(true),
    );
    expect(calls.find((c) => c.url.endsWith('/approve'))!.body).toEqual({
      reason: 'Rapport relu, conforme',
    });
  });

  it('Restaurer : le demandeur ne peut pas approuver sa propre demande', async () => {
    const own = {
      ...RESTORE,
      actions: { ...RESTORE.actions, approve: false, approve_blocked: 'same_admin' },
    };
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/restores/op-1')) return jsonResponse(own);
      if (url.includes('/restores?')) return jsonResponse(page([own]));
      if (url.includes('/overview')) return jsonResponse(OVERVIEW);
      return jsonResponse(page([]));
    });
    renderAt('restore');
    fireEvent.click((await screen.findAllByText('Entreprise alpha')).at(-1)!);
    expect(((await screen.findByTestId('restore-approve')) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(screen.getByText(/AUTRE administrateur/)).toBeTruthy();
  });

  it('Paramètres : jour et heure modifiés, raison, prochaine exécution affichée', async () => {
    renderAt('settings');
    const next = await screen.findByTestId('physical-weekly-next');
    expect(next.textContent).toContain('Prochaine exécution');
    fill('physical-weekly-time', '22:30');
    fireEvent.click(screen.getByTestId('physical-weekly-save'));
    fill('decision-reason', 'Sauvegarde du soir');
    fireEvent.click(screen.getByTestId('reason-accept'));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    const put = calls.find((c) => c.method === 'PUT')!;
    expect(put.url).toContain('/policies/physical/weekly');
    expect(put.body).toMatchObject({
      run_time: '22:30',
      weekday: 7,
      timezone: 'UTC',
      reason: 'Sauvegarde du soir',
    });
  });

  it('PITR : instant saisi en UTC explicite, type affiché d’une sauvegarde', () => {
    expect(utcInputToIso('2026-11-04T10:15')).toBe('2026-11-04T10:15:00Z');
    expect(utcInputToIso('04/11/2026')).toBeNull();
    expect(itemType(ITEM)).toBe('daily');
    expect(itemType({ ...ITEM, run_frequency: null, run_trigger: 'pre_restore' })).toBe(
      'pre_restore',
    );
    expect(itemType({ ...ITEM, run_frequency: null, run_trigger: 'retry' })).toBe('manual');
  });
});
