import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import type { Sale } from '@/modules/sales/api';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { DocumentStatusBadge } from '@/shared/ui/StatusBadge';

import { useRecentSales } from './api';

/** Widget « Dernières ventes » (`sales:recent`). */
export function RecentSalesPanel() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const recentSales = useRecentSales(true);
  return (
    <Card title={t('dashboard.recentSales')}>
      {recentSales.isError ? (
        <ErrorMessage error={recentSales.error} onRetry={() => void recentSales.refetch()} />
      ) : (
        <DataTable
          className="sm-table sm-table--compact"
          value={recentSales.data?.items ?? []}
          loading={recentSales.isPending}
          dataKey="id"
          rowHover
          rowClassName={() => 'sm-row-clickable'}
          onRowClick={(e) => void navigate(`/sales/${(e.data as Sale).id}`)}
          tableStyle={{ minWidth: '30rem' }}
          emptyMessage={<EmptyState icon="pi pi-shopping-cart" title={t('sales.empty')} />}
        >
          <Column field="number" header={t('sales.number')} />
          <Column header={t('sales.date')} body={(s: Sale) => formatDate(s.sale_date, locale)} />
          <Column
            header={t('sales.customer')}
            body={(s: Sale) => s.customer_name ?? t('sales.anonymousShort')}
          />
          <Column
            header={t('sales.total')}
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(s: Sale) => formatMoney(s.total, currency, locale)}
          />
          <Column
            header={t('sales.status')}
            body={(s: Sale) => <DocumentStatusBadge labels="sales.statuses" status={s.status} />}
          />
        </DataTable>
      )}
      <div className="sm-form-actions">
        <Button
          label={t('dashboard.allSales')}
          icon="pi pi-arrow-right"
          iconPos="right"
          text
          onClick={() => void navigate('/sales')}
        />
      </div>
    </Card>
  );
}
