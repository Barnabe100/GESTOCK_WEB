import { Column } from 'primereact/column';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatDateTime } from '@/shared/lib/format';
import { INITIAL_TABLE, type TableState } from '@/shared/lib/serverTable';
import { EmptyState } from '@/shared/ui/EmptyState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { ServerTable } from '@/shared/ui/ServerTable';

import { useAuditLogs, type AuditLog } from './api';

/** Valeur lisible d'un détail d'audit (les structures restent compactes). */
function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** Détails d'une entrée : paires « clé : valeur » plutôt qu'un bloc JSON brut. */
function AuditDetails({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) return null;
  return (
    <dl className="sm-kv">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd className={typeof value === 'object' && value !== null ? 'sm-code' : undefined}>
            {display(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default function AuditLogPage() {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const logs = useAuditLogs(table.rows, table.first);
  const { locale, timezone } = capabilities.tenant;

  return (
    <>
      <PageHeader title={t('audit.title')} description={t('audit.subtitle')} />
      <ServerTable
        query={logs}
        table={table}
        onTableChange={setTable}
        minWidth="56rem"
        empty={<EmptyState icon="pi pi-history" title={t('audit.empty')} />}
      >
        <Column
          header={t('audit.date')}
          bodyClassName="sm-nowrap"
          body={(l: AuditLog) => formatDateTime(l.occurred_at, locale, timezone)}
        />
        <Column
          header={t('audit.user')}
          body={(l: AuditLog) => l.user_name ?? l.user_email ?? t('audit.system')}
        />
        <Column field="action" header={t('audit.action')} bodyClassName="sm-nowrap" />
        <Column header={t('audit.entity')} body={(l: AuditLog) => l.entity_type ?? ''} />
        <Column
          header={t('audit.details')}
          body={(l: AuditLog) => <AuditDetails data={l.data} />}
        />
      </ServerTable>
    </>
  );
}
