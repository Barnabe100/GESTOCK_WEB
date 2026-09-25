import { Dialog } from 'primereact/dialog';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { SalePaymentBadge } from '@/modules/sales/ui';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';

import { useRecentPosSales } from './api';

/** Dernières ventes du point de vente sur le site (API Ventes, aucune donnée dupliquée). */
export function RecentSalesDialog({ siteId, onClose }: { siteId: string; onClose: () => void }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const sales = useRecentPosSales(siteId, true);

  return (
    <Dialog header={t('pos.recentSales')} visible onHide={onClose} className="sm-dialog">
      {sales.isPending ? (
        <LoadingState />
      ) : sales.isError ? (
        <ErrorMessage error={sales.error} onRetry={() => void sales.refetch()} />
      ) : sales.data.items.length === 0 ? (
        <EmptyState icon="pi pi-shopping-cart" title={t('pos.noRecentSales')} />
      ) : (
        <ul className="sm-pos-choices">
          {sales.data.items.map((s) => (
            <li key={s.id}>
              <Link to={`/sales/${s.id}`} className="sm-pos-choice" onClick={onClose}>
                <span className="sm-pos-choice-main">
                  <strong>{`${s.number} · ${formatMoney(s.total, currency, locale)}`}</strong>
                  <span className="sm-muted">
                    {[
                      formatDateTime(s.validated_at ?? s.created_at, locale, timezone),
                      s.customer_name ?? t('sales.anonymousShort'),
                    ].join(' · ')}
                  </span>
                </span>
                {s.payment_status && <SalePaymentBadge status={s.payment_status} />}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
