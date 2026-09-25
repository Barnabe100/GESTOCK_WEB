import { Column } from 'primereact/column';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { formatDateTime } from '@/shared/lib/format';
import { INITIAL_TABLE, useDebouncedValue, type TableState } from '@/shared/lib/serverTable';
import { EmptyState } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';

import { AuditChanges } from '../auditDisplay';
import { useAudit } from '../queries';
import type { PlatformAuditEntry } from '../types';

export function PlatformAuditPage() {
  const { t } = useTranslation();
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [action, setAction] = useState('');
  const filter = useDebouncedValue(action.trim());
  const logs = useAudit(table.rows, table.first, { action: filter });

  return (
    <>
      <PageHeader title={t('console:audit.title')} description={t('console:audit.subtitle')} />
      <FilterBar>
        <SearchInput
          value={action}
          onChange={(value) => {
            setAction(value);
            setTable((s) => ({ ...s, first: 0 }));
          }}
          placeholder={t('console:audit.filterAction')}
        />
      </FilterBar>
      <ServerTable
        query={logs}
        table={table}
        onTableChange={setTable}
        minWidth="60rem"
        empty={<EmptyState icon="pi pi-history" title={t('console:audit.empty')} />}
      >
        <Column
          header={t('console:audit.date')}
          bodyClassName="sm-nowrap"
          body={(e: PlatformAuditEntry) => formatDateTime(e.occurred_at)}
        />
        <Column field="actor_label" header={t('console:audit.actor')} />
        <Column field="action" header={t('console:audit.action')} bodyClassName="sm-nowrap" />
        <Column
          header={t('console:audit.target')}
          body={(e: PlatformAuditEntry) =>
            e.target_type ? `${e.target_type} · ${e.target_id ?? ''}` : '—'
          }
        />
        <Column
          header={t('console:audit.changes')}
          body={(e: PlatformAuditEntry) => <AuditChanges entry={e} />}
        />
        <Column field="reason" header={t('console:audit.reason')} />
      </ServerTable>
    </>
  );
}
