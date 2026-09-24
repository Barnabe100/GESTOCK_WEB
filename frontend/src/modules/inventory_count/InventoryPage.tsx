import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Dialog } from 'primereact/dialog';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { ProgressBar } from 'primereact/progressbar';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import { confirmAction } from '@/shared/ui/confirm';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { LoadingState } from '@/shared/ui/LoadingState';
import { MetricCard } from '@/shared/ui/MetricCard';
import { NotFound } from '@/shared/ui/NotFound';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';

import { useInventory, useInventoryMutations, type Inventory } from './api';
import { InventoryLinesTable } from './InventoryLinesTable';
import { InventoryStatusBadge } from './ui';

const P = 'inventory_count.inventory';

function CancelDialog({ inventory, onClose }: { inventory: Inventory; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { cancel } = useInventoryMutations();
  const [reason, setReason] = useState('');
  const submit = () =>
    cancel.mutate(
      { id: inventory.id, reason: reason.trim() },
      {
        onSuccess: () => {
          toast.success(t('inventories.cancelled', { number: inventory.number }));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  return (
    <Dialog header={t('inventories.cancel')} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        <Message severity="warn" text={t('inventories.cancelInfo')} />
        <FormField
          id="cancel-reason"
          label={t('stock.cancellationReason')}
          help={t('stock.cancellationReasonHelp')}
          required
        >
          <InputTextarea
            id="cancel-reason"
            rows={3}
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
            autoFocus
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.back')} text onClick={onClose} />
          <Button
            label={t('inventories.cancel')}
            severity="danger"
            disabled={reason.trim().length < 5}
            loading={cancel.isPending}
            onClick={submit}
          />
        </div>
      </div>
    </Dialog>
  );
}

/** Informations générales : site, type, dates et auteurs des étapes. */
function InventoryInfo({ inventory }: { inventory: Inventory }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const when = (date: string | null, name: string | null) =>
    date ? `${formatDateTime(date, locale, timezone)}${name ? ` — ${name}` : ''}` : null;
  const rows: [string, string | null][] = [
    [t('layout.site'), inventory.site_name],
    [t('inventories.type'), t(`inventories.types.${inventory.inventory_type}`)],
    [t('inventories.createdAt'), when(inventory.created_at, inventory.created_by_name)],
    [t('inventories.startedAt'), when(inventory.started_at, inventory.started_by_name)],
    [t('inventories.completedAt'), when(inventory.completed_at, inventory.completed_by_name)],
    [t('inventories.validatedAt'), when(inventory.validated_at, inventory.validated_by_name)],
    [t('inventories.cancelledAt'), when(inventory.cancelled_at, inventory.cancelled_by_name)],
    [t('stock.cancellationReason'), inventory.cancellation_reason],
    [t('stock.comment'), inventory.comment],
  ];
  return (
    <Card className="sm-block">
      <dl className="sm-details">
        {rows
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
      </dl>
    </Card>
  );
}

/** Résumé des écarts (avant validation : sur le stock courant ; après : figé). */
function InventorySummaryCards({ inventory }: { inventory: Inventory }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const s = inventory.summary;
  if (!s) return null;
  const { currency, locale } = capabilities.tenant;
  return (
    <>
      <section className="sm-metrics sm-block" aria-label={t('inventories.summary')}>
        <MetricCard icon="pi pi-list" value={s.lines} label={t('inventories.summaryLines')} />
        <MetricCard
          icon="pi pi-check-square"
          tone={s.counted === s.lines ? 'success' : 'warning'}
          value={`${s.counted} / ${s.lines}`}
          label={t('inventories.summaryCounted')}
        />
        <MetricCard
          icon="pi pi-arrow-up"
          tone="info"
          value={s.surplus}
          label={t('inventories.summarySurplus')}
          hint={formatMoney(s.surplus_value, currency, locale)}
        />
        <MetricCard
          icon="pi pi-arrow-down"
          tone="danger"
          value={s.shortage}
          label={t('inventories.summaryShortage')}
          hint={formatMoney(s.shortage_value, currency, locale)}
        />
        <MetricCard
          icon="pi pi-equals"
          tone="success"
          value={s.no_variance}
          label={t('inventories.summaryNoVariance')}
        />
      </section>
      <p className="sm-help">
        {t(s.final ? 'inventories.summaryFinal' : 'inventories.summaryProjected', {
          value: formatMoney(s.adjustment_value, currency, locale),
        })}
      </p>
    </>
  );
}

export default function InventoryPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const { id } = useParams();
  const query = useInventory(id === 'new' ? undefined : id);
  const { start, complete, reopen, validate } = useInventoryMutations();
  const [cancelling, setCancelling] = useState(false);

  // « new » sans la permission de création : la route de création n'existe pas pour ce rôle.
  if (id === 'new') return <NotFound />;
  if (query.isPending) return <LoadingState />;
  if (query.isError) {
    return <ErrorMessage error={query.error} onRetry={() => void query.refetch()} />;
  }
  const inventory = query.data;
  const { status } = inventory;
  const s = inventory.summary;
  const open = status === 'DRAFT' || status === 'COUNTING' || status === 'READY_TO_VALIDATE';
  const canCount = can(`${P}.count`);
  const onError = (error: unknown) => toast.error(translateError(t, error));
  const allCounted = s !== null && s.counted === s.lines;

  const confirmValidation = () =>
    confirmAction(t, {
      header: t('inventories.validateTitle', { number: inventory.number }),
      message: t('inventories.confirmValidate', {
        lines: s?.lines ?? 0,
        counted: s?.counted ?? 0,
        surplus: s?.surplus ?? 0,
        shortage: s?.shortage ?? 0,
        none: s?.no_variance ?? 0,
      }),
      acceptLabel: t('inventories.validate'),
      onAccept: () =>
        validate.mutate(inventory.id, {
          onSuccess: (done) => toast.success(t('inventories.validated', { number: done.number })),
          onError,
        }),
    });

  const actions = (
    <div className="sm-tags">
      <InventoryStatusBadge status={status} />
      {status === 'DRAFT' && canCount && (
        <Button
          icon="pi pi-play"
          label={t('inventories.start')}
          loading={start.isPending}
          onClick={() => start.mutate(inventory.id, { onError })}
        />
      )}
      {status === 'COUNTING' && canCount && (
        <Button
          icon="pi pi-check"
          label={t('inventories.completeCounting')}
          disabled={!allCounted}
          loading={complete.isPending}
          onClick={() => complete.mutate(inventory.id, { onError })}
        />
      )}
      {status === 'READY_TO_VALIDATE' && canCount && (
        <Button
          icon="pi pi-pencil"
          label={t('inventories.reopenCounting')}
          outlined
          loading={reopen.isPending}
          onClick={() => reopen.mutate(inventory.id, { onError })}
        />
      )}
      {status === 'READY_TO_VALIDATE' && can(`${P}.validate`) && (
        <Button
          icon="pi pi-verified"
          label={t('inventories.validate')}
          loading={validate.isPending}
          onClick={confirmValidation}
        />
      )}
      {open && can(`${P}.cancel`) && (
        <Button
          icon="pi pi-times"
          label={t('inventories.cancel')}
          severity="danger"
          outlined
          onClick={() => setCancelling(true)}
        />
      )}
    </div>
  );

  return (
    <>
      <PageHeader
        title={`${t('inventories.one')} ${inventory.number}`}
        breadcrumbs={[
          { label: t('inventories.title'), to: '/inventories' },
          { label: inventory.number },
        ]}
        actions={actions}
      />
      {status === 'DRAFT' && (
        <Message
          severity="info"
          className="sm-block"
          text={t(`inventories.draftInfo.${inventory.inventory_type}`)}
        />
      )}
      {status === 'COUNTING' && s && (
        <div className="sm-block sm-progress">
          <p className="sm-strong" id="count-progress">
            {t('inventories.progress', { counted: s.counted, total: s.lines })}
          </p>
          <ProgressBar
            value={s.lines ? Math.round((s.counted * 100) / s.lines) : 0}
            showValue={false}
            aria-labelledby="count-progress"
          />
          {!allCounted && canCount && (
            <small className="sm-help">{t('inventories.completeHint')}</small>
          )}
        </div>
      )}
      {status === 'READY_TO_VALIDATE' && (
        <Message severity="warn" className="sm-block" text={t('inventories.readyInfo')} />
      )}
      {status === 'VALIDATED' && (
        <div className="sm-block sm-tags">
          <Message severity="success" text={t('inventories.validatedInfo')} />
          <Link to={`/stock/movements?search=${encodeURIComponent(inventory.number)}`}>
            {t('inventories.seeMovements')}
          </Link>
        </div>
      )}
      {status === 'CANCELLED' && (
        <Message severity="warn" className="sm-block" text={t('inventories.cancelledInfo')} />
      )}
      <InventoryInfo inventory={inventory} />
      {(status === 'READY_TO_VALIDATE' || status === 'VALIDATED') && (
        <InventorySummaryCards inventory={inventory} />
      )}
      {/* Colonnes propres à chaque statut : nouveau tableau à chaque transition. */}
      <InventoryLinesTable key={status} inventory={inventory} />
      {cancelling && <CancelDialog inventory={inventory} onClose={() => setCancelling(false)} />}
    </>
  );
}
