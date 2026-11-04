// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '@/core/i18n';
import { jsonResponse } from '@/shared/testing';

import { BackupsPage } from './pages/BackupsPage';
import type { BackupOperation, BackupOverview, BackupStatus } from './types';

const op = (operation: string, extra: Partial<BackupOperation> = {}): BackupOperation => ({
  operation,
  last_status: 'success',
  last_started_at: '2026-11-02T01:15:00Z',
  last_finished_at: '2026-11-02T01:20:00Z',
  last_message: 'ok',
  last_success_at: '2026-11-02T01:20:00Z',
  last_success_age_seconds: 3600,
  max_age_seconds: 691200,
  problem: null,
  level: 'ok',
  details: {},
  errors: [],
  checks: [],
  ...extra,
});

const STATUS: BackupStatus = {
  checked_at: '2026-11-02T10:00:00Z',
  level: 'warning',
  status_configured: true,
  rpo_seconds: 900,
  thresholds: {
    wal_max_lag_seconds: 900,
    base_max_age_hours: 192,
    verify_max_age_hours: 26,
    restore_test_max_age_days: 35,
    offsite_max_age_hours: 48,
    disk_warn_free_percent: 15,
    disk_critical_free_percent: 5,
  },
  archiving: {
    archive_mode: 'on',
    archive_timeout_seconds: 300,
    wal_level: 'replica',
    data_checksums: false,
    archived_count: 120,
    last_archived_wal: '000000010000000000000078',
    last_archived_at: '2026-11-01T09:58:00Z',
    last_archived_age_seconds: 86400,
    failed_count: 0,
    last_failed_wal: null,
    last_failed_at: null,
    failure_unresolved: false,
    pending_segments: 0,
    state: 'idle',
    level: 'ok',
    messages: [],
    ok: true,
  },
  operations: {
    base_backup: op('base_backup', { details: { label: '20261102T011500Z' } }),
    verify: op('verify'),
    restore_test: op('restore_test', {
      details: { rto_seconds: 42 },
      checks: [
        { name: 'pg_amcheck', ok: true },
        { name: 'rls_tenant_isolation', ok: true },
      ],
    }),
    prune: op('prune'),
  },
  storage: {
    configured: true,
    generated_at: '2026-11-02T02:15:00Z',
    base_backups: 2,
    base_backups_verified: 2,
    base_backups_bytes: 104857600,
    latest_verified_backup: '20261102T011500Z',
    restorable_from: '2026-10-26T01:20:00Z',
    wal_files: 300,
    wal_bytes: 5242880,
    retention_days: 30,
    disk_free_bytes: 10737418240,
    disk_free_percent: 40,
    same_filesystem_as_data: true,
    restore_test_dirs: 0,
    level: 'warning',
    messages: ['same_filesystem_as_data'],
  },
  offsite: {
    configured: false,
    repository: null,
    last_status: 'not_configured',
    last_success_at: null,
    last_success_age_seconds: null,
    level: 'warning',
    problem: 'not_configured',
    details: {},
  },
  supervision: {
    last_check_at: '2026-11-02T09:58:00Z',
    age_seconds: 120,
    reported_level: 'warning',
    stale: false,
    level: 'warning',
  },
};

const OVERVIEW: BackupOverview = {
  checked_at: '2026-11-02T10:00:00Z',
  level: 'ok',
  schema_revision: '0063',
  agent: {
    last_seen_at: '2026-11-02T09:59:30Z',
    age_seconds: 30,
    agent: 'agent@db',
    last_error: null,
    level: 'ok',
    problem: null,
  },
  schedules: [],
  active_runs: [],
  active_restores: [],
  window: { known: false, restorable_from: null, restorable_to: null },
};

const fetchMock = vi.fn<typeof fetch>();
/** Statuts de R11-0 (`/operations/backups`) et vue d'ensemble de l'agent (R11). */
const serve = (status: unknown) =>
  fetchMock.mockImplementation(async (input) =>
    jsonResponse(String(input).includes('/overview') ? OVERVIEW : status),
  );
beforeEach(() => vi.stubGlobal('fetch', fetchMock));
afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const renderPage = () =>
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter>
        <BackupsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('console — sauvegardes (lecture seule, niveaux du serveur)', () => {
  it('base inactive sans alerte, opérations, copie externe non configurée, trois actions', async () => {
    serve(STATUS);
    renderPage();
    const state = await screen.findByTestId('backups-archiving-state');
    expect(state.textContent).toContain('base inactive');
    expect(screen.getByTestId('backups-archiving').textContent).toContain('24 h');
    expect(screen.getByText(/sommes de contrôle des pages désactivées/i)).toBeTruthy();
    expect(screen.getByText(/ne les remplacent pas/)).toBeTruthy();
    const restore = screen.getByTestId('backups-op-restore_test');
    expect(restore.textContent).toContain('2 / 2 contrôles réussis');
    expect(restore.textContent).toContain('42 s');
    expect(screen.getByText(/Copie externe NON CONFIGURÉE/)).toBeTruthy();
    expect(screen.getByText(/même système de fichiers/)).toBeTruthy();
    expect(screen.getByTestId('backups-storage').textContent).toContain('2 dont 2 vérifiée(s)');
    // Trois actions : Sauvegarder, Restaurer, Paramètres des sauvegardes.
    expect(screen.getByTestId('action-backup').textContent).toContain('Sauvegarder');
    expect(screen.getByTestId('action-restore').textContent).toContain('Restaurer');
    expect(screen.getByTestId('action-settings').textContent).toContain('Paramètres');
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/operations/backups'))).toBe(
      true,
    );
  });

  it('échec non résolu, retard, opération en échec et supervision arrêtée', async () => {
    serve({
      ...STATUS,
      level: 'critical',
      archiving: {
        ...STATUS.archiving,
        state: 'failing',
        level: 'critical',
        failure_unresolved: true,
        failed_count: 3,
        last_failed_at: '2026-11-02T09:59:00Z',
      },
      operations: {
        ...STATUS.operations,
        verify: op('verify', {
          level: 'critical',
          problem: 'last_run_failed',
          last_status: 'failed',
          last_message: '1 anomalie',
          errors: ['1 segment(s) WAL manquant(s)'],
        }),
      },
      supervision: { ...STATUS.supervision, stale: true, level: 'critical' },
    });
    renderPage();
    expect((await screen.findByTestId('backups-archiving-state')).textContent).toContain(
      'NON RÉSOLU',
    );
    expect(screen.getByTestId('backups-archiving').textContent).toContain('non résolu');
    const verify = screen.getByTestId('backups-op-verify');
    expect(verify.textContent).toContain('Dernière exécution en échec');
    expect(verify.textContent).toContain('WAL manquant');
    expect(screen.getByText(/Supervision arrêtée/)).toBeTruthy();
    expect(screen.getByTestId('backups-overall').textContent).toContain('Alerte');
  });

  it('statuts non montés et retard signalés', async () => {
    serve({
      ...STATUS,
      status_configured: false,
      archiving: {
        ...STATUS.archiving,
        state: 'lagging',
        level: 'critical',
        pending_segments: 4,
      },
    });
    renderPage();
    expect((await screen.findByTestId('backups-archiving-state')).textContent).toContain(
      'RETARD : 4 segments',
    );
    expect(screen.getByText(/status\/ non monté/)).toBeTruthy();
  });
});
