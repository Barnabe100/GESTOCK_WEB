import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { ProgressBar } from 'primereact/progressbar';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { INITIAL_TABLE, toQueryString, type TableState } from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { DateRangeFilter, FilterBar } from '@/shared/ui/FilterBar';
import { ServerTable } from '@/shared/ui/ServerTable';
import { useToast } from '@/shared/ui/toast';

import { useBackupRun, useBackupRuns, useCancelBackupRun } from '../queries';
import type { BackupCategory, BackupRun, BackupRunStatus, BackupTrigger } from '../types';
import { ReasonDialog, RunStatusBadge, TenantPicker, bytes, when } from './shared';

const KINDS: BackupCategory[] = ['physical', 'logical', 'verification', 'restore_test'];
const STATUSES: BackupRunStatus[] = ['queued', 'running', 'succeeded', 'failed', 'cancelled'];
const TRIGGERS: BackupTrigger[] = ['schedule', 'manual', 'retry', 'pre_restore', 'restore_source'];

/** Historique global ou par entreprise des exécutions (filtres côté serveur). */
export function RunsPanel() {
  const { t } = useTranslation();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'requested_at',
    sortOrder: -1,
  });
  const [kind, setKind] = useState<BackupCategory | null>(null);
  const [status, setStatus] = useState<BackupRunStatus | null>(null);
  const [trigger, setTrigger] = useState<BackupTrigger | null>(null);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [range, setRange] = useState({ from: '', to: '' });
  const [open, setOpen] = useState<string | null>(null);
  const runs = useBackupRuns(
    toQueryString(table, {
      kind,
      status,
      trigger,
      tenant_id: tenantId,
      date_from: range.from,
      date_to: range.to,
    }),
  );
  const filtered = Boolean(kind || status || trigger || tenantId || range.from || range.to);
  const first = () => setTable((s) => ({ ...s, first: 0 }));
  const option = (prefix: string, values: string[], all: string) => [
    { label: all, value: null },
    ...values.map((v) => ({ label: t(`${prefix}.${v}`), value: v })),
  ];
  return (
    <>
      <FilterBar
        active={filtered}
        onReset={() => {
          setKind(null);
          setStatus(null);
          setTrigger(null);
          setTenantId(null);
          setRange({ from: '', to: '' });
          first();
        }}
      >
        <TenantPicker
          id="runs-tenant"
          value={tenantId}
          allowAll
          onChange={(v) => {
            setTenantId(v);
            first();
          }}
        />
        <Dropdown
          aria-label={t('console:bk.kind')}
          data-testid="runs-kind"
          value={kind}
          options={option('console:bk.kinds', KINDS, t('console:bk.allKinds'))}
          onChange={(e) => {
            setKind(e.value as BackupCategory | null);
            first();
          }}
        />
        <Dropdown
          aria-label={t('console:bk.trigger')}
          value={trigger}
          options={option('console:bk.triggers', TRIGGERS, t('console:bk.allTriggers'))}
          onChange={(e) => {
            setTrigger(e.value as BackupTrigger | null);
            first();
          }}
        />
        <Dropdown
          aria-label={t('console:bk.status')}
          value={status}
          options={option('console:bk.runStatus', STATUSES, t('console:bk.allStatuses'))}
          onChange={(e) => {
            setStatus(e.value as BackupRunStatus | null);
            first();
          }}
        />
        <DateRangeFilter
          from={range.from}
          to={range.to}
          onChange={(r) => {
            setRange(r);
            first();
          }}
        />
      </FilterBar>
      <ServerTable
        query={runs}
        table={table}
        onTableChange={setTable}
        minWidth="60rem"
        onRowClick={(r: BackupRun) => setOpen(r.id)}
        empty={
          <ListEmpty filtered={filtered} icon="pi pi-history" title={t('console:bk.runsEmpty')} />
        }
      >
        <Column
          field="requested_at"
          sortable
          header={t('console:bk.requestedAt')}
          bodyClassName="sm-nowrap"
          body={(r: BackupRun) => when(r.requested_at)}
        />
        <Column
          header={t('console:bk.kind')}
          body={(r: BackupRun) => (
            <span>
              <span className="sm-strong">{t(`console:bk.kinds.${r.kind}`)}</span>
              <br />
              <small className="sm-muted">
                {r.tenant_name ?? t('console:bk.allTenants')}
                {r.items_total > 0 && ` · ${r.items_succeeded}/${r.items_total}`}
              </small>
            </span>
          )}
        />
        <Column
          header={t('console:bk.trigger')}
          body={(r: BackupRun) =>
            `${t(`console:bk.triggers.${r.trigger}`)}${r.frequency ? ` (${t(`console:bk.frequencies.${r.frequency}`)})` : ''}${r.attempt > 1 ? ` · ${t('console:bk.attempt', { n: r.attempt, max: r.max_attempts })}` : ''}`
          }
        />
        <Column
          header={t('console:bk.status')}
          body={(r: BackupRun) => (
            <span>
              <RunStatusBadge status={r.status} />
              {r.status === 'running' && (
                <small className="sm-muted"> {r.progress_percent} %</small>
              )}
            </span>
          )}
        />
        <Column
          field="finished_at"
          sortable
          header={t('console:bk.finishedAt')}
          bodyClassName="sm-nowrap"
          body={(r: BackupRun) => when(r.finished_at)}
        />
      </ServerTable>
      {open && <RunDetailDialog id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function RunDetailDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const run = useBackupRun(id);
  const cancel = useCancelBackupRun();
  const [cancelling, setCancelling] = useState(false);
  const data = run.data;
  return (
    <Dialog
      header={t('console:bk.runTitle')}
      visible
      onHide={onClose}
      className="sm-dialog"
      style={{ width: 'min(48rem, 95vw)' }}
      footer={
        <>
          {data?.can_cancel && (
            <Button
              label={t('console:bk.cancelRun')}
              severity="danger"
              outlined
              onClick={() => setCancelling(true)}
            />
          )}
          <Button label={t('console:bk.close')} onClick={onClose} />
        </>
      }
    >
      {!data ? (
        <ProgressBar mode="indeterminate" style={{ height: '0.5rem' }} />
      ) : (
        <>
          <dl className="sm-details" data-testid="run-detail">
            <div>
              <dt>{t('console:bk.status')}</dt>
              <dd>
                <RunStatusBadge status={data.status} /> {data.progress_message ?? ''}
              </dd>
            </div>
            <div>
              <dt>{t('console:bk.kind')}</dt>
              <dd>
                {t(`console:bk.kinds.${data.kind}`)} —{' '}
                {data.tenant_name ?? t('console:bk.allTenants')}
              </dd>
            </div>
            <div>
              <dt>{t('console:bk.trigger')}</dt>
              <dd>
                {t(`console:bk.triggers.${data.trigger}`)} · {data.requested_label}
              </dd>
            </div>
            <div>
              <dt>{t('console:bk.timeline')}</dt>
              <dd>
                {when(data.requested_at)} → {when(data.started_at)} → {when(data.finished_at)}
              </dd>
            </div>
            {data.error && (
              <div>
                <dt>{t('console:bk.error')}</dt>
                <dd className="sm-danger">{data.error}</dd>
              </div>
            )}
          </dl>
          {(data.status === 'running' || data.status === 'queued') && (
            <ProgressBar value={data.progress_percent} />
          )}
          {data.items.length > 0 && (
            <table className="sm-simple-table" data-testid="run-items">
              <thead>
                <tr>
                  <th>{t('console:bk.tenant')}</th>
                  <th>{t('console:bk.status')}</th>
                  <th>{t('console:bk.content')}</th>
                  <th>{t('console:bk.expiresAt')}</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id}>
                    <td>{item.tenant_name}</td>
                    <td>{t(`console:bk.itemStatus.${item.status}`)}</td>
                    <td>
                      {item.status === 'succeeded'
                        ? `${t('console:bk.tables', { count: item.table_count ?? 0 })} · ${t('console:bk.rows', { count: item.row_count ?? 0 })} · ${bytes(item.size_bytes)}`
                        : (item.error ?? '—')}
                    </td>
                    <td>{when(item.expires_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {Object.keys(data.result).length > 0 && (
            <details>
              <summary>{t('console:bk.rawResult')}</summary>
              <pre className="sm-pre">{JSON.stringify(data.result, null, 2)}</pre>
            </details>
          )}
        </>
      )}
      {cancelling && data && (
        <ReasonDialog
          header={t('console:bk.cancelRun')}
          acceptLabel={t('console:bk.cancelRun')}
          danger
          pending={cancel.isPending}
          error={cancel.error}
          onClose={() => setCancelling(false)}
          onAccept={(reason) =>
            cancel.mutate(
              { id: data.id, reason },
              {
                onSuccess: () => {
                  toast.success(t('console:bk.runCancelled'));
                  setCancelling(false);
                  void run.refetch();
                },
              },
            )
          }
        />
      )}
    </Dialog>
  );
}
