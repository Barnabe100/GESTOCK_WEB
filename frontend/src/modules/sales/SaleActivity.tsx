import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { useMovements, type Movement } from '@/modules/stock/api';
import { formatMoney, formatQuantity } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';

import { useSaleHistory, type Sale, type SaleEvent } from './api';

/**
 * Mouvements de stock générés par la vente (`stock.movement.view`, sites du périmètre : le
 * serveur filtre). Affiché seulement pour une vente validée ou annulée.
 */
export function SaleMovementsPanel({ sale }: { sale: Sale }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const params = new URLSearchParams({
    source_type: 'sale',
    source_id: sale.id,
    limit: '100',
    sort: 'occurred_at',
  });
  const movements = useMovements(params.toString());

  return (
    <section className="sm-block" aria-labelledby="sale-movements-title">
      <div className="sm-section-header">
        <h2 id="sale-movements-title">{t('sales.movements.title')}</h2>
      </div>
      <p className="sm-help">{t('sales.movements.help')}</p>
      {movements.isPending ? (
        <LoadingState />
      ) : movements.isError ? (
        <ErrorMessage error={movements.error} onRetry={() => void movements.refetch()} />
      ) : (
        <Card>
          <DataTable
            className="sm-table"
            value={movements.data.items}
            dataKey="id"
            tableStyle={{ minWidth: '40rem' }}
            emptyMessage={<EmptyState icon="pi pi-box" title={t('sales.movements.empty')} />}
          >
            <Column
              header={t('sales.movements.date')}
              body={(m: Movement) => formatDateTime(m.occurred_at, locale, timezone)}
            />
            <Column
              header={t('sales.movements.article')}
              body={(m: Movement) => `${m.article_reference} — ${m.article_designation}`}
            />
            <Column
              header={t('sales.movements.type')}
              body={(m: Movement) => t(`stock.movementTypes.${m.movement_type}`)}
            />
            <Column
              header={t('sales.movements.quantity')}
              headerClassName="sm-num"
              bodyClassName="sm-num"
              body={(m: Movement) => `${formatQuantity(m.quantity, locale)} ${m.unit}`}
            />
            <Column
              header={t('sales.movements.after')}
              headerClassName="sm-num"
              bodyClassName="sm-num"
              body={(m: Movement) => formatQuantity(m.quantity_after, locale)}
            />
            <Column header={t('sales.movements.user')} body={(m: Movement) => m.user_name ?? '—'} />
          </DataTable>
        </Card>
      )}
    </section>
  );
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}

/**
 * Chronologie de la vente (`audit.log.view`) : uniquement les évènements réellement
 * journalisés (vente et paiements), du plus ancien au plus récent.
 */
export function SaleHistoryPanel({ sale }: { sale: Sale }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const history = useSaleHistory(sale.id, true);

  const details = (event: SaleEvent): string => {
    const parts: string[] = [];
    const number = text(event.data.number);
    if (event.action.startsWith('payment.') && number) parts.push(number);
    const amount = text(event.data.amount) ?? text(event.data.excess);
    if (amount) parts.push(formatMoney(amount, currency, locale));
    const method = text(event.data.method_label);
    if (method) parts.push(method);
    const reason = text(event.data.reason);
    if (reason) parts.push(reason);
    return parts.join(' — ');
  };

  return (
    <section className="sm-block" aria-labelledby="sale-history-title">
      <div className="sm-section-header">
        <h2 id="sale-history-title">{t('sales.history.title')}</h2>
      </div>
      <p className="sm-help">{t('sales.history.help')}</p>
      {history.isPending ? (
        <LoadingState />
      ) : history.isError ? (
        <ErrorMessage error={history.error} onRetry={() => void history.refetch()} />
      ) : history.data.length === 0 ? (
        <EmptyState icon="pi pi-history" title={t('sales.history.empty')} />
      ) : (
        <ol className="sm-timeline" aria-label={t('sales.history.title')}>
          {history.data.map((event) => (
            <li key={event.id} className="sm-timeline-item">
              <time dateTime={event.occurred_at} className="sm-help">
                {formatDateTime(event.occurred_at, locale, timezone)}
              </time>
              <strong>
                {t(`sales.history.actions.${event.action}`, { defaultValue: event.action })}
              </strong>
              <span>{event.user_name ?? t('sales.history.system')}</span>
              {details(event) && <span className="sm-help">{details(event)}</span>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
