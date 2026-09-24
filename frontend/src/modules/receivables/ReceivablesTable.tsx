import type { UseQueryResult } from '@tanstack/react-query';
import { Column } from 'primereact/column';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { SalePaymentBadge } from '@/modules/sales/ui';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import type { Page, TableState } from '@/shared/lib/serverTable';
import { RowActions } from '@/shared/ui/RowActions';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusBadge } from '@/shared/ui/StatusBadge';

import type { Receivable } from './api';

/**
 * Tableau des créances ouvertes (liste générale et fiche client) : montants calculés par le
 * serveur, ouverture de la fiche de vente (articles, paiements, solde dû).
 */
export function ReceivablesTable({
  query,
  table,
  onTableChange,
  empty,
  showCustomer = true,
}: {
  query: UseQueryResult<Page<Receivable>>;
  table: TableState;
  onTableChange: (state: TableState) => void;
  empty: ReactNode;
  showCustomer?: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const multiSite = capabilities.site === null && capabilities.sites.length > 1;
  const money = (value: string) => formatMoney(value, currency, locale);
  const canOpen = can('sales.sale.view');
  const open = (r: Receivable) => void navigate(`/sales/${r.sale_id}`);

  return (
    <ServerTable
      query={query}
      table={table}
      onTableChange={onTableChange}
      dataKey="sale_id"
      minWidth="52rem"
      onRowClick={canOpen ? open : undefined}
      empty={empty}
    >
      {showCustomer && (
        <Column
          field="customer_name"
          header={t('receivables.customer')}
          sortable
          body={(r: Receivable) =>
            r.customer_name ? (
              <div className="sm-tags">
                <span>{r.customer_name}</span>
                {r.customer_is_active === false && (
                  <StatusBadge tone="neutral" label={t('common.inactive')} />
                )}
              </div>
            ) : (
              <span className="sm-muted">{t('receivables.noCustomer')}</span>
            )
          }
        />
      )}
      <Column
        field="sale_number"
        header={t('receivables.sale')}
        sortable
        bodyClassName="sm-nowrap"
      />
      <Column
        field="sale_date"
        header={t('receivables.saleDate')}
        sortable
        body={(r: Receivable) => formatDate(r.sale_date, locale, 'UTC')}
      />
      {multiSite && <Column field="site_name" header={t('layout.site')} />}
      <Column
        field="total"
        header={t('receivables.total')}
        sortable
        headerClassName="sm-num"
        bodyClassName="sm-num"
        body={(r: Receivable) => money(r.total)}
      />
      <Column
        field="paid_amount"
        header={t('receivables.paid')}
        sortable
        headerClassName="sm-num"
        bodyClassName="sm-num"
        body={(r: Receivable) => money(r.paid_amount)}
      />
      <Column
        field="remaining_amount"
        header={t('receivables.remaining')}
        sortable
        headerClassName="sm-num"
        bodyClassName="sm-num sm-strong"
        body={(r: Receivable) => money(r.remaining_amount)}
      />
      <Column
        header={t('receivables.state')}
        body={(r: Receivable) => <SalePaymentBadge status={r.payment_status} />}
      />
      {canOpen && (
        <Column
          header={t('common.actions')}
          body={(r: Receivable) => (
            <RowActions
              actions={[
                {
                  key: 'open',
                  label: t('sales.open'),
                  icon: 'pi pi-eye',
                  onClick: () => open(r),
                },
              ]}
            />
          )}
        />
      )}
    </ServerTable>
  );
}
