import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  useReleaseActivation,
  useSiteActivations,
  type LicenseActivation,
  type LicenseSummary,
} from './api';

/** Libération d'un poste : raison obligatoire, confirmation explicite, un seul envoi. */
function ReleaseDialog({
  activation,
  onClose,
}: {
  activation: LicenseActivation;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const release = useReleaseActivation();
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [reasonError, setReasonError] = useState(false);
  const title = t('subscriptionPage.postes.releaseTitle', { label: activation.label });
  return (
    <Dialog header={title} visible onHide={onClose} className="sm-dialog">
      <form
        className="sm-form"
        noValidate
        aria-label={title}
        onSubmit={(e) => {
          e.preventDefault();
          if (release.isPending) return;
          if (!reason.trim()) {
            setReasonError(true);
            return;
          }
          release.mutate(
            { id: activation.id, reason: reason.trim() },
            {
              onSuccess: () => {
                toast.success(t('subscriptionPage.postes.released'));
                onClose();
              },
            },
          );
        }}
      >
        <Message severity="info" text={t('subscriptionPage.postes.releaseHelp')} />
        {release.error && <Message severity="error" text={translateError(t, release.error)} />}
        <FormField
          id="release-reason"
          label={t('subscriptionPage.postes.reason')}
          required
          error={reasonError ? t('subscriptionPage.postes.reasonRequired') : undefined}
        >
          <InputTextarea
            id="release-reason"
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
            inputId="release-confirm"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.checked === true)}
          />
          <label htmlFor="release-confirm">{t('subscriptionPage.postes.confirmCheck')}</label>
        </div>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            severity="danger"
            label={t('subscriptionPage.postes.release')}
            disabled={!confirmed}
            loading={release.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}

/**
 * Postes du site : autorisés (licence), utilisés, disponibles, et liste des postes actifs.
 * Le Web n'active aucun poste : l'activation se fait depuis l'application installée.
 */
export function SitePostes({
  siteId,
  license,
  locale,
}: {
  siteId: string;
  license: LicenseSummary;
  locale: string;
}) {
  const { t } = useTranslation();
  const { can } = useCapabilities();
  const activations = useSiteActivations(siteId);
  const [releasing, setReleasing] = useState<LicenseActivation | null>(null);
  const canRelease = can('subscription.activation.manage');

  return (
    <div className="sm-block" data-testid={`postes-${siteId}`}>
      <p className="sm-strong" data-testid="license-activations">
        {t('subscriptionPage.postes.summary', {
          allowed: t('subscriptionPage.postes.allowed', { count: license.max_activations }),
          used: t('subscriptionPage.postes.used', { count: license.activations_used }),
          available: t('subscriptionPage.postes.available', {
            count: license.activations_available,
          }),
        })}
      </p>
      {activations.isError ? (
        <ErrorMessage error={activations.error} onRetry={() => void activations.refetch()} />
      ) : (
        <DataTable
          className="sm-table"
          tableStyle={{ minWidth: '36rem' }}
          value={activations.data?.items ?? []}
          loading={activations.isFetching}
          dataKey="id"
          emptyMessage={
            <EmptyState
              icon="pi pi-desktop"
              title={t('subscriptionPage.postes.empty')}
              description={t('subscriptionPage.postes.emptyHelp')}
            />
          }
        >
          <Column field="label" header={t('subscriptionPage.postes.label')} />
          <Column
            header={t('subscriptionPage.postes.version')}
            body={(a: LicenseActivation) => a.client_version ?? '—'}
          />
          <Column
            header={t('subscriptionPage.postes.activatedAt')}
            body={(a: LicenseActivation) => formatDateTime(a.activated_at, locale)}
          />
          <Column
            header={t('subscriptionPage.postes.lastSeen')}
            body={(a: LicenseActivation) => (
              <span>
                {formatDateTime(a.last_seen_at, locale)}{' '}
                {a.stale && (
                  <StatusBadge tone="warning" label={t('subscriptionPage.postes.stale')} />
                )}
              </span>
            )}
          />
          {canRelease && (
            <Column
              header=""
              body={(a: LicenseActivation) => (
                <Button
                  icon="pi pi-power-off"
                  text
                  severity="danger"
                  label={t('subscriptionPage.postes.release')}
                  aria-label={`${t('subscriptionPage.postes.release')} ${a.label}`}
                  onClick={() => setReleasing(a)}
                />
              )}
            />
          )}
        </DataTable>
      )}
      {releasing && <ReleaseDialog activation={releasing} onClose={() => setReleasing(null)} />}
    </div>
  );
}
