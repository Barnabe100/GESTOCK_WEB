// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '@/core/i18n';
import { jsonResponse } from '@/shared/testing';

import { BackupsPage } from './pages/BackupsPage';
import type { BackupStatus } from './types';

const STATUS: BackupStatus = {
  checked_at: '2026-11-02T10:00:00Z',
  rpo_seconds: 900,
  archiving: {
    archive_mode: 'on',
    archive_timeout_seconds: 300,
    wal_level: 'replica',
    archived_count: 120,
    last_archived_wal: '000000010000000000000078',
    last_archived_at: '2026-11-02T09:58:00Z',
    last_archived_age_seconds: 120,
    failed_count: 0,
    last_failed_wal: null,
    last_failed_at: null,
    ok: true,
  },
  base_backups: {
    configured: false,
    count: 0,
    latest_label: null,
    latest_finished_at: null,
    latest_age_hours: null,
    unverified_count: 0,
    ok: false,
    max_age_hours: 26,
  },
};

const fetchMock = vi.fn<typeof fetch>();
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
      <BackupsPage />
    </QueryClientProvider>,
  );

describe('console — sauvegardes (lecture seule)', () => {
  it('archivage conforme ; répertoire non monté signalé ; aucune action proposée', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(STATUS));
    renderPage();
    const archiving = await screen.findByTestId('backups-archiving');
    expect(archiving.textContent).toContain('on');
    expect(archiving.textContent).toContain('120 s');
    expect(screen.getByText('Conforme')).toBeTruthy();
    expect(screen.getByText(/non monté dans la console/)).toBeTruthy();
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/operations/backups');
  });

  it('sauvegarde de base trop ancienne : « À vérifier »', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse({
        ...STATUS,
        base_backups: {
          ...STATUS.base_backups,
          configured: true,
          count: 3,
          latest_label: 'stockmanager-20261031T011500Z',
          latest_finished_at: '2026-10-31T01:20:00Z',
          latest_age_hours: 56,
        },
      }),
    );
    renderPage();
    const base = await screen.findByTestId('backups-base');
    expect(base.textContent).toContain('stockmanager-20261031T011500Z');
    expect(base.textContent).toContain('56 h');
    expect(screen.getByText('À vérifier')).toBeTruthy();
  });
});
