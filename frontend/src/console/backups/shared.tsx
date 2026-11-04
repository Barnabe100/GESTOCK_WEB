import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { formatDateTime } from '@/shared/lib/format';
import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';
import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import { useTenants } from '../queries';
import type { BackupRunStatus, RestoreStatus } from '../types';

export const RUN_TONES: Record<BackupRunStatus, Tone> = {
  queued: 'neutral',
  running: 'info',
  succeeded: 'success',
  failed: 'danger',
  cancelled: 'neutral',
};

export const RESTORE_TONES: Record<RestoreStatus, Tone> = {
  requested: 'neutral',
  verifying: 'info',
  verified: 'warning',
  verification_failed: 'danger',
  approved: 'info',
  applying: 'info',
  applied: 'success',
  apply_failed: 'danger',
  rejected: 'neutral',
  cancelled: 'neutral',
  expired: 'neutral',
};

export function RunStatusBadge({ status }: { status: BackupRunStatus }) {
  const { t } = useTranslation();
  return <StatusBadge tone={RUN_TONES[status]} label={t(`console:bk.runStatus.${status}`)} />;
}

export function RestoreStatusBadge({ status }: { status: RestoreStatus }) {
  const { t } = useTranslation();
  return (
    <StatusBadge tone={RESTORE_TONES[status]} label={t(`console:bk.restoreStatus.${status}`)} />
  );
}

export const when = (iso: string | null | undefined, never = '—') =>
  iso ? formatDateTime(iso, 'fr') : never;

/** Instant en UTC explicite (planification de la plateforme). */
export const whenUtc = (iso: string | null | undefined) =>
  iso ? `${formatDateTime(iso, 'fr', 'UTC')} UTC` : '—';

export function bytes(value: number | null | undefined) {
  if (value === null || value === undefined) return '—';
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} Kio`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} Mio`;
  return `${(value / 1024 ** 3).toFixed(2)} Gio`;
}

/** Choix d'une entreprise (liste de la console, recherche côté serveur). */
export function TenantPicker({
  id,
  value,
  onChange,
  allowAll = false,
}: {
  id: string;
  value: string | null;
  onChange: (tenantId: string | null) => void;
  allowAll?: boolean;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const params = new URLSearchParams({ limit: '50', offset: '0' });
  if (search.trim()) params.set('search', search.trim());
  const tenants = useTenants(params.toString());
  const options = [
    ...(allowAll ? [{ label: t('console:bk.allTenants'), value: null }] : []),
    ...(tenants.data?.items ?? []).map((tenant) => ({ label: tenant.name, value: tenant.id })),
  ];
  return (
    <Dropdown
      inputId={id}
      data-testid={id}
      value={value}
      options={options}
      filter
      onFilter={(e) => setSearch(e.filter)}
      placeholder={t('console:bk.chooseTenant')}
      emptyMessage={t('console:bk.noTenant')}
      onChange={(e) => onChange((e.value as string | null) ?? null)}
    />
  );
}

/**
 * Décision motivée (approbation, refus, annulation, retour arrière) : raison obligatoire et,
 * pour une action irréversible, confirmation explicite. Le serveur revérifie tout.
 */
export function ReasonDialog({
  header,
  summary,
  warning,
  acceptLabel,
  danger = false,
  requireConfirmation = false,
  pending,
  error,
  onAccept,
  onClose,
}: {
  header: string;
  summary?: ReactNode;
  warning?: string;
  acceptLabel: string;
  danger?: boolean;
  requireConfirmation?: boolean;
  pending: boolean;
  error: unknown;
  onAccept: (reason: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [touched, setTouched] = useState(false);
  const tooShort = reason.trim().length < 5;
  return (
    <Dialog
      header={header}
      visible
      onHide={onClose}
      className="sm-dialog"
      style={{ width: 'min(36rem, 95vw)' }}
      footer={
        <>
          <Button label={t('actions.cancel')} text severity="secondary" onClick={onClose} />
          <Button
            label={acceptLabel}
            severity={danger ? 'danger' : undefined}
            loading={pending}
            disabled={requireConfirmation && !confirmed}
            data-testid="reason-accept"
            onClick={() => {
              setTouched(true);
              if (!tooShort) onAccept(reason.trim());
            }}
          />
        </>
      }
    >
      {summary}
      {warning && <Message severity="warn" text={warning} className="sm-block" />}
      <FormField
        id="decision-reason"
        label={t('console:bk.reason')}
        required
        error={touched && tooShort ? t('console:bk.reasonRequired') : undefined}
      >
        <InputTextarea
          id="decision-reason"
          data-testid="decision-reason"
          value={reason}
          rows={3}
          autoResize
          onChange={(e) => setReason(e.target.value)}
        />
      </FormField>
      {requireConfirmation && (
        <div className="sm-checkbox-row">
          <Checkbox
            inputId="decision-confirm"
            data-testid="decision-confirm"
            checked={confirmed}
            onChange={(e) => setConfirmed(Boolean(e.checked))}
          />
          <label htmlFor="decision-confirm">{t('console:bk.confirmIrreversible')}</label>
        </div>
      )}
      {error !== null && error !== undefined && (
        <Message severity="error" text={translateError(t, error)} className="sm-block" />
      )}
    </Dialog>
  );
}
