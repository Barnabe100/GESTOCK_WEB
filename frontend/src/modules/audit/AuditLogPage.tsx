import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatDateTime } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { PageHeader } from '@/shared/ui/PageHeader';

import { useAuditLogs, type AuditLog } from './api';

const PAGE_SIZE = 25;

export default function AuditLogPage() {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const [first, setFirst] = useState(0);
  const logs = useAuditLogs(PAGE_SIZE, first);
  const { locale, timezone } = capabilities.tenant;

  return (
    <>
      <PageHeader title={t('audit.title')} />
      {logs.isError ? (
        <ErrorMessage error={logs.error} onRetry={() => void logs.refetch()} />
      ) : (
        <DataTable
          value={logs.data?.items ?? []}
          loading={logs.isFetching}
          dataKey="id"
          lazy
          paginator
          first={first}
          rows={PAGE_SIZE}
          totalRecords={logs.data?.total ?? 0}
          onPage={(e) => setFirst(e.first)}
          emptyMessage={t('common.noData')}
        >
          <Column
            header={t('audit.date')}
            body={(l: AuditLog) => formatDateTime(l.occurred_at, locale, timezone)}
          />
          <Column
            header={t('audit.user')}
            body={(l: AuditLog) => l.user_name ?? l.user_email ?? t('audit.system')}
          />
          <Column field="action" header={t('audit.action')} />
          <Column
            header={t('audit.entity')}
            body={(l: AuditLog) => (l.entity_type ? `${l.entity_type}` : '')}
          />
          <Column
            header={t('audit.details')}
            body={(l: AuditLog) => (
              <code className="sm-code">
                {Object.keys(l.data).length ? JSON.stringify(l.data) : ''}
              </code>
            )}
          />
        </DataTable>
      )}
    </>
  );
}
