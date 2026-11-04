import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { SelectButton } from 'primereact/selectbutton';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { INITIAL_TABLE, toQueryString, type TableState } from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { DateRangeFilter, FilterBar } from '@/shared/ui/FilterBar';
import { FormField } from '@/shared/ui/FormField';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import { useBackupItems, useBackupOverview, useRequestRestore, useRestores } from '../queries';
import type {
  BackupFrequency,
  BackupItem,
  BackupTrigger,
  RestoreOperation,
  RestoreStatus,
} from '../types';
import { RestoreDetailDialog } from './RestoreDetailDialog';
import { ReasonDialog, RestoreStatusBadge, TenantPicker, bytes, when, whenUtc } from './shared';

const TYPES: { trigger?: BackupTrigger; frequency?: BackupFrequency; key: string }[] = [
  { key: 'daily', frequency: 'daily' },
  { key: 'weekly', frequency: 'weekly' },
  { key: 'monthly', frequency: 'monthly' },
  { key: 'automatic', frequency: 'automatic' },
  { key: 'manual', trigger: 'manual' },
  { key: 'pre_restore', trigger: 'pre_restore' },
  { key: 'restore_source', trigger: 'restore_source' },
];
const ITEM_STATUSES = ['succeeded', 'failed', 'expired'] as const;
const RESTORE_STATUSES: RestoreStatus[] = [
  'requested',
  'verifying',
  'verified',
  'verification_failed',
  'approved',
  'applying',
  'applied',
  'apply_failed',
  'rejected',
  'cancelled',
  'expired',
];

/** Type affiché d'une sauvegarde : fréquence planifiée, sinon origine de la demande. */
export function itemType(item: BackupItem): string {
  if (item.run_frequency) return item.run_frequency;
  return item.run_trigger === 'pre_restore' || item.run_trigger === 'restore_source'
    ? item.run_trigger
    : 'manual';
}

/** `AAAA-MM-JJTHH:MM` saisi en UTC → ISO 8601 UTC (jamais l'heure locale implicite). */
export function utcInputToIso(value: string): string | null {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) ? `${value}:00Z` : null;
}

/**
 * « Restaurer » : choisir une sauvegarde dans l'historique (entreprise, date, période, type,
 * statut) ou un instant (PITR) dans la fenêtre restaurable. Toute demande est d'abord VÉRIFIÉE
 * par l'agent sur une copie isolée (intégrité, WAL, contrôles), puis approuvée par un second
 * administrateur ; rien n'est jamais appliqué directement.
 */
export function RestorePanel() {
  const { t } = useTranslation();
  const [mode, setMode] = useState<'logical' | 'pitr'>('logical');
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      <Message severity="info" text={t('console:bk.restoreIntro')} className="sm-block" />
      <SelectButton
        value={mode}
        allowEmpty={false}
        options={[
          { label: t('console:bk.modes.logical'), value: 'logical' },
          { label: t('console:bk.modes.pitr'), value: 'pitr' },
        ]}
        onChange={(e) => setMode(e.value as 'logical' | 'pitr')}
        data-testid="restore-mode"
      />
      {mode === 'logical' ? (
        <ItemsSection onCreated={setOpen} />
      ) : (
        <PitrSection onCreated={setOpen} />
      )}
      <h3>{t('console:bk.restoresTitle')}</h3>
      <RestoresTable onOpen={setOpen} />
      {open && <RestoreDetailDialog id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function ItemsSection({ onCreated }: { onCreated: (id: string) => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const request = useRequestRestore();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'created_at',
    sortOrder: -1,
  });
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [type, setType] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>('succeeded');
  const [range, setRange] = useState({ from: '', to: '' });
  const [chosen, setChosen] = useState<BackupItem | null>(null);
  const selected = TYPES.find((x) => x.key === type);
  const items = useBackupItems(
    toQueryString(table, {
      tenant_id: tenantId,
      status,
      trigger: selected?.trigger,
      frequency: selected?.frequency,
      date_from: range.from,
      date_to: range.to,
    }),
  );
  const filtered = Boolean(tenantId || type || status !== 'succeeded' || range.from || range.to);
  const first = () => setTable((s) => ({ ...s, first: 0 }));
  return (
    <>
      <FilterBar
        active={filtered}
        onReset={() => {
          setTenantId(null);
          setType(null);
          setStatus('succeeded');
          setRange({ from: '', to: '' });
          first();
        }}
      >
        <TenantPicker
          id="items-tenant"
          value={tenantId}
          allowAll
          onChange={(v) => {
            setTenantId(v);
            first();
          }}
        />
        <Dropdown
          aria-label={t('console:bk.type')}
          data-testid="items-type"
          value={type}
          options={[
            { label: t('console:bk.allTypes'), value: null },
            ...TYPES.map((x) => ({ label: t(`console:bk.types.${x.key}`), value: x.key })),
          ]}
          onChange={(e) => {
            setType(e.value as string | null);
            first();
          }}
        />
        <Dropdown
          aria-label={t('console:bk.status')}
          value={status}
          options={[
            { label: t('console:bk.allStatuses'), value: null },
            ...ITEM_STATUSES.map((s) => ({ label: t(`console:bk.itemStatus.${s}`), value: s })),
          ]}
          onChange={(e) => {
            setStatus(e.value as string | null);
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
        query={items}
        table={table}
        onTableChange={setTable}
        minWidth="64rem"
        empty={
          <ListEmpty filtered={filtered} icon="pi pi-database" title={t('console:bk.itemsEmpty')} />
        }
      >
        <Column
          field="created_at"
          sortable
          header={t('console:bk.date')}
          bodyClassName="sm-nowrap"
          body={(i: BackupItem) => when(i.created_at)}
        />
        <Column
          header={t('console:bk.tenant')}
          body={(i: BackupItem) => <span className="sm-strong">{i.tenant_name}</span>}
        />
        <Column
          header={t('console:bk.type')}
          body={(i: BackupItem) => t(`console:bk.types.${itemType(i)}`)}
        />
        <Column
          header={t('console:bk.content')}
          body={(i: BackupItem) =>
            i.status === 'succeeded'
              ? `${t('console:bk.rows', { count: i.row_count ?? 0 })} · ${bytes(i.size_bytes)}`
              : (i.error ?? '—')
          }
        />
        <Column
          header={t('console:bk.integrity')}
          body={(i: BackupItem) =>
            i.restorable ? (
              <StatusBadge
                tone="success"
                icon="pi pi-shield"
                label={t('console:bk.verifiedSums')}
              />
            ) : (
              <StatusBadge
                tone="neutral"
                label={t(`console:bk.notRestorable.${i.restorable_reason ?? 'not_succeeded'}`)}
              />
            )
          }
        />
        <Column
          header={t('console:bk.actionsCol')}
          body={(i: BackupItem) => (
            <Button
              label={t('console:bk.restoreThis')}
              icon="pi pi-replay"
              size="small"
              outlined
              disabled={!i.restorable}
              onClick={() => setChosen(i)}
              data-testid={`restore-item-${i.id}`}
            />
          )}
        />
      </ServerTable>
      {chosen && (
        <ReasonDialog
          header={t('console:bk.requestRestore')}
          summary={
            <dl className="sm-details">
              <div>
                <dt>{t('console:bk.tenant')}</dt>
                <dd>{chosen.tenant_name}</dd>
              </div>
              <div>
                <dt>{t('console:bk.date')}</dt>
                <dd>{when(chosen.created_at)}</dd>
              </div>
              <div>
                <dt>{t('console:bk.content')}</dt>
                <dd>
                  {t('console:bk.tables', { count: chosen.table_count ?? 0 })} ·{' '}
                  {t('console:bk.rows', { count: chosen.row_count ?? 0 })} ·{' '}
                  <code>{(chosen.manifest_sha256 ?? '').slice(0, 12)}</code>
                </dd>
              </div>
            </dl>
          }
          warning={t('console:bk.requestWarning')}
          acceptLabel={t('console:bk.requestRestore')}
          pending={request.isPending}
          error={request.error}
          onClose={() => setChosen(null)}
          onAccept={(reason) =>
            request.mutate(
              {
                scope: 'tenant',
                tenant_id: chosen.tenant_id,
                mode: 'logical',
                source_item_id: chosen.id,
                reason,
              },
              {
                onSuccess: (op) => {
                  toast.success(t('console:bk.requested'));
                  setChosen(null);
                  onCreated(op.id);
                },
              },
            )
          }
        />
      )}
    </>
  );
}

function PitrSection({ onCreated }: { onCreated: (id: string) => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const overview = useBackupOverview();
  const request = useRequestRestore();
  const [scope, setScope] = useState<'tenant' | 'platform'>('tenant');
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [target, setTarget] = useState('');
  const [asking, setAsking] = useState(false);
  const window = overview.data?.window;
  const iso = utcInputToIso(target);
  const ready = iso !== null && (scope === 'platform' || tenantId !== null);
  return (
    <div className="sm-form-grid" data-testid="pitr-form">
      {window && !window.known && <Message severity="warn" text={t('console:bk.windowUnknown')} />}
      {window?.known && (
        <p data-testid="pitr-window">
          {t('console:bk.window', {
            from: whenUtc(window.restorable_from),
            to: whenUtc(window.restorable_to),
          })}
        </p>
      )}
      <FormField id="pitr-scope" label={t('console:bk.pitrScope')}>
        <SelectButton
          id="pitr-scope"
          value={scope}
          allowEmpty={false}
          options={[
            { label: t('console:bk.oneTenant'), value: 'tenant' },
            { label: t('console:bk.platformRestore'), value: 'platform' },
          ]}
          onChange={(e) => setScope(e.value as 'tenant' | 'platform')}
        />
      </FormField>
      {scope === 'platform' && <Message severity="warn" text={t('console:bk.platformWarning')} />}
      {scope === 'tenant' && (
        <FormField id="pitr-tenant" label={t('console:bk.tenant')} required>
          <TenantPicker id="pitr-tenant" value={tenantId} onChange={setTenantId} />
        </FormField>
      )}
      <FormField
        id="pitr-target"
        label={t('console:bk.targetUtc')}
        required
        help={t('console:bk.targetHelp')}
      >
        <InputText
          id="pitr-target"
          type="datetime-local"
          data-testid="pitr-target"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
        />
      </FormField>
      <div>
        <Button
          label={t('console:bk.requestRestore')}
          icon="pi pi-replay"
          disabled={!ready}
          onClick={() => setAsking(true)}
          data-testid="pitr-request"
        />
      </div>
      {asking && iso && (
        <ReasonDialog
          header={t('console:bk.requestRestore')}
          summary={
            <p>
              {scope === 'platform' ? t('console:bk.platformRestore') : t('console:bk.oneTenant')} —{' '}
              {whenUtc(iso)}
            </p>
          }
          warning={t(
            scope === 'platform' ? 'console:bk.platformWarning' : 'console:bk.requestWarning',
          )}
          acceptLabel={t('console:bk.requestRestore')}
          pending={request.isPending}
          error={request.error}
          onClose={() => setAsking(false)}
          onAccept={(reason) =>
            request.mutate(
              {
                scope,
                tenant_id: scope === 'tenant' ? tenantId : null,
                mode: 'pitr',
                target_time: iso,
                reason,
              },
              {
                onSuccess: (op) => {
                  toast.success(t('console:bk.requested'));
                  setAsking(false);
                  onCreated(op.id);
                },
              },
            )
          }
        />
      )}
    </div>
  );
}

function RestoresTable({ onOpen }: { onOpen: (id: string) => void }) {
  const { t } = useTranslation();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'requested_at',
    sortOrder: -1,
  });
  const [status, setStatus] = useState<RestoreStatus | null>(null);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const restores = useRestores(toQueryString(table, { status, tenant_id: tenantId }));
  const filtered = Boolean(status || tenantId);
  return (
    <>
      <FilterBar
        active={filtered}
        onReset={() => {
          setStatus(null);
          setTenantId(null);
        }}
      >
        <TenantPicker id="restores-tenant" value={tenantId} allowAll onChange={setTenantId} />
        <Dropdown
          aria-label={t('console:bk.status')}
          value={status}
          options={[
            { label: t('console:bk.allStatuses'), value: null },
            ...RESTORE_STATUSES.map((s) => ({
              label: t(`console:bk.restoreStatus.${s}`),
              value: s,
            })),
          ]}
          onChange={(e) => setStatus(e.value as RestoreStatus | null)}
        />
      </FilterBar>
      <ServerTable
        query={restores}
        table={table}
        onTableChange={setTable}
        minWidth="56rem"
        onRowClick={(r: RestoreOperation) => onOpen(r.id)}
        empty={
          <ListEmpty
            filtered={filtered}
            icon="pi pi-replay"
            title={t('console:bk.restoresEmpty')}
          />
        }
      >
        <Column
          field="requested_at"
          sortable
          header={t('console:bk.requestedAt')}
          bodyClassName="sm-nowrap"
          body={(r: RestoreOperation) => when(r.requested_at)}
        />
        <Column
          header={t('console:bk.target')}
          body={(r: RestoreOperation) =>
            r.scope === 'platform' ? t('console:bk.platformRestore') : r.tenant_name
          }
        />
        <Column
          header={t('console:bk.modeCol')}
          body={(r: RestoreOperation) =>
            `${t(`console:bk.modes.${r.mode}`)}${r.target_time ? ` · ${whenUtc(r.target_time)}` : ''}${r.rollback_of ? ` · ${t('console:bk.isRollback')}` : ''}`
          }
        />
        <Column
          header={t('console:bk.requestedBy')}
          body={(r: RestoreOperation) => r.requested_label}
        />
        <Column
          header={t('console:bk.status')}
          body={(r: RestoreOperation) => <RestoreStatusBadge status={r.status} />}
        />
      </ServerTable>
    </>
  );
}
