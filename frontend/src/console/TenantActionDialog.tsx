import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';
import { useToast } from '@/shared/ui/toast';

import { useTenantAction } from './queries';
import { tenantDate } from './tenantDisplay';
import type { TenantAction, TenantDetail, TenantSubscription } from './types';

export type ActionKind = TenantAction['kind'];

/**
 * Action TechNova sur une entreprise : récapitulatif, champs propres à l'action (dates, plan),
 * raison obligatoire, confirmation explicite. Les propositions (dates) viennent du serveur, qui
 * revérifie l'état, les dates et le plan : il fait foi.
 */
export function TenantActionDialog({
  tenant,
  kind,
  subscription,
  onClose,
}: {
  tenant: TenantDetail;
  kind: ActionKind;
  /** Abonnement visé (activation, prolongation, changement de plan) : celui d'un site. */
  subscription?: TenantSubscription;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const action = useTenantAction(tenant.id);
  const [reason, setReason] = useState('');
  const proposals = subscription?.actions;
  const [start, setStart] = useState(proposals?.activation_start ?? '');
  const [end, setEnd] = useState(
    (kind === 'extend' ? proposals?.extension_end : proposals?.activation_end) ?? '',
  );
  const [plan, setPlan] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [reasonError, setReasonError] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const danger = kind === 'suspend';

  const summary: [string, string][] = [[t('console:tenant.summaryTenant'), tenant.name]];
  if (subscription) {
    summary.push([
      t('console:tenant.summarySite'),
      subscription.site?.name ?? t('console:tenant.unattached'),
    ]);
    summary.push([t('console:tenant.summaryPlan'), subscription.plan_name]);
  }
  if (kind === 'extend' && subscription) {
    summary.push([
      t('console:tenant.summaryCurrentEnd'),
      tenantDate(subscription.current_period_end, tenant.timezone),
    ]);
  }
  const help: Partial<Record<ActionKind, string>> = {
    suspend: t('console:tenant.suspendHelp'),
    reactivate: t('console:tenant.reactivateHelp'),
    activate: t('console:tenant.transitional'),
    extend: t('console:tenant.extendHelp'),
    'change-plan': t('console:tenant.planChangeHelp'),
  };

  const payload = (): TenantAction => {
    const trimmed = reason.trim();
    switch (kind) {
      case 'activate':
        return {
          kind,
          subscription_id: subscription?.id ?? '',
          reason: trimmed,
          period_start: start,
          period_end: end,
        };
      case 'extend':
        return { kind, subscription_id: subscription?.id ?? '', reason: trimmed, period_end: end };
      case 'change-plan':
        return {
          kind,
          subscription_id: subscription?.id ?? '',
          reason: trimmed,
          plan_code: plan ?? '',
        };
      default:
        return { kind, reason: trimmed };
    }
  };

  const submit = () => {
    if (!reason.trim()) {
      setReasonError(true);
      return;
    }
    setError(null);
    action.mutate(payload(), {
      onSuccess: () => {
        toast.success(t('console:tenant.done'));
        onClose();
      },
      onError: setError,
    });
  };

  const ready = confirmed && (kind !== 'change-plan' || plan !== null);
  return (
    <Dialog
      header={t(`console:tenant.dialogTitle.${kind}`)}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form
        className="sm-form"
        noValidate
        aria-label={t(`console:tenant.dialogTitle.${kind}`)}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <dl className="sm-details">
          {summary.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        {help[kind] && (
          <Message severity={danger || kind === 'activate' ? 'warn' : 'info'} text={help[kind]} />
        )}
        {error !== null && <Message severity="error" text={translateError(t, error)} />}
        {kind === 'activate' && (
          <FormField id="period-start" label={t('console:tenant.periodStartField')} required>
            <InputText
              id="period-start"
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </FormField>
        )}
        {(kind === 'activate' || kind === 'extend') && (
          <FormField id="period-end" label={t('console:tenant.periodEndField')} required>
            <InputText
              id="period-end"
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </FormField>
        )}
        {kind === 'change-plan' && (
          <FormField id="new-plan" label={t('console:tenant.newPlan')} required>
            <Dropdown
              inputId="new-plan"
              value={plan}
              options={(proposals?.available_plans ?? []).map((p) => ({
                label: p.name,
                value: p.code,
              }))}
              placeholder={t('console:tenant.choosePlan')}
              onChange={(e) => setPlan(e.value as string)}
            />
          </FormField>
        )}
        <FormField
          id="action-reason"
          label={t('console:tenant.reason')}
          required
          help={t('console:tenant.reasonHelp')}
          error={reasonError ? t('console:tenant.reasonRequired') : undefined}
        >
          <InputTextarea
            id="action-reason"
            rows={2}
            maxLength={500}
            value={reason}
            invalid={reasonError}
            onChange={(e) => {
              setReason(e.target.value);
              setReasonError(false);
            }}
            autoFocus
          />
        </FormField>
        <div className="sm-checkbox">
          <Checkbox
            inputId="action-confirm"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.checked === true)}
          />
          <label htmlFor="action-confirm">{t('console:tenant.confirmCheck')}</label>
        </div>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            label={t(`console:tenant.confirm.${kind}`)}
            severity={danger ? 'danger' : undefined}
            disabled={!ready}
            loading={action.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}
