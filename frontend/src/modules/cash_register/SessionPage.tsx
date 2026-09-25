import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams, useSearchParams } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { MetricCard } from '@/shared/ui/MetricCard';
import { PageHeader } from '@/shared/ui/PageHeader';

import { useCashSession } from './api';
import { CashJournal } from './CashJournal';
import { CloseSessionDialog } from './CloseSessionDialog';
import { MovementDialog } from './MovementDialog';
import { CashSessionBadge, CashVariance } from './ui';

/**
 * Session de caisse : résumé (fond initial, encaissements, sorties, solde théorique ; après
 * clôture : compté et écart), entrées / sorties manuelles, clôture, journal complet. Une
 * session clôturée est en lecture seule.
 */
export default function SessionPage() {
  const { t } = useTranslation();
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const { can, capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const session = useCashSession(id);
  const [movement, setMovement] = useState<'in' | 'out' | null>(null);
  const [closing, setClosing] = useState(params.get('close') === '1');

  if (session.isPending) return <LoadingState />;
  if (session.isError) {
    return <ErrorMessage error={session.error} onRetry={() => void session.refetch()} />;
  }
  const s = session.data;
  const open = s.status === 'OPEN';
  const money = (value: string) => formatMoney(value, currency, locale);
  const canMove = open && can('cash_register.movement.create');
  const canClose = open && can('cash_register.session.close');

  return (
    <>
      <PageHeader
        title={t('cash.sessionTitle', { number: s.number })}
        description={`${s.cash_register_name} (${s.cash_register_code}) · ${s.site_name}`}
        breadcrumbs={[
          { label: t('cash.sessionsTitle'), to: '/cash/sessions' },
          { label: s.number },
        ]}
        actions={
          <div className="sm-tags">
            <CashSessionBadge status={s.status} />
            {canMove && (
              <>
                <Button
                  icon="pi pi-plus"
                  label={t('cash.recordCashIn')}
                  outlined
                  onClick={() => setMovement('in')}
                />
                <Button
                  icon="pi pi-minus"
                  label={t('cash.recordCashOut')}
                  outlined
                  severity="warning"
                  onClick={() => setMovement('out')}
                />
              </>
            )}
            {canClose && (
              <Button
                icon="pi pi-lock"
                label={t('cash.close')}
                severity="danger"
                onClick={() => setClosing(true)}
              />
            )}
          </div>
        }
      />
      <div className="sm-metrics" role="group" aria-label={t('cash.sessionSummary')}>
        <MetricCard
          icon="pi pi-inbox"
          value={money(s.opening_float)}
          label={t('cash.openingFloat')}
        />
        <MetricCard
          icon="pi pi-arrow-down"
          tone="success"
          value={money(s.cash_in_total)}
          label={t('cash.cashInTotal')}
        />
        <MetricCard
          icon="pi pi-arrow-up"
          tone="warning"
          value={money(s.cash_out_total)}
          label={t('cash.cashOutTotal')}
        />
        <MetricCard
          icon="pi pi-wallet"
          tone="info"
          value={money(s.theoretical_balance)}
          label={t('cash.theoreticalBalance')}
          hint={t('cash.movementCount', { count: s.movement_count })}
        />
      </div>
      <Card title={t('cash.tracking')} className="sm-block">
        <dl className="sm-details">
          <div>
            <dt>{t('cash.openedAt')}</dt>
            <dd>{`${formatDateTime(s.opened_at, locale, timezone)} · ${s.opened_by_name ?? ''}`}</dd>
          </div>
          {s.closed_at && (
            <>
              <div>
                <dt>{t('cash.closedAt')}</dt>
                <dd>{`${formatDateTime(s.closed_at, locale, timezone)} · ${s.closed_by_name ?? ''}`}</dd>
              </div>
              <div>
                <dt>{t('cash.countedBalance')}</dt>
                <dd>{s.counted_balance ? money(s.counted_balance) : '—'}</dd>
              </div>
              <div>
                <dt>{t('cash.variance.label')}</dt>
                <dd>
                  <CashVariance value={s.variance} currency={currency} locale={locale} />
                </dd>
              </div>
              {s.closing_note && (
                <div>
                  <dt>{t('cash.closingNote')}</dt>
                  <dd>{s.closing_note}</dd>
                </div>
              )}
            </>
          )}
        </dl>
        {!open && <p className="sm-help">{t('cash.closedHelp')}</p>}
      </Card>
      <section className="sm-block" aria-labelledby="journal-title">
        <div className="sm-section-header">
          <h2 id="journal-title">{t('cash.journal')}</h2>
        </div>
        <CashJournal sessionId={s.id} />
      </section>
      {movement && (
        <MovementDialog session={s} direction={movement} onClose={() => setMovement(null)} />
      )}
      {closing && open && can('cash_register.session.close') && (
        <CloseSessionDialog
          session={s}
          onClose={() => {
            setClosing(false);
            if (params.has('close')) setParams({}, { replace: true });
          }}
        />
      )}
    </>
  );
}
