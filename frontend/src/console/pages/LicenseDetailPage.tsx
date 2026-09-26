import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Message } from 'primereact/message';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';

import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { LicenseStateBadge, StatusBadge } from '@/shared/ui/StatusBadge';

import { AuditChanges } from '../auditDisplay';
import { CONSOLE_BASE } from '../ConsoleLayout';
import { LicenseActionDialog, ReleaseActivationDialog } from '../LicenseDialogs';
import { downloadLicense, useAudit, useLicense, useSubscriptionActivations } from '../queries';
import { Details, paymentDay } from '../tenantDisplay';
import type { ConsoleActivation, ConsoleLicense, PlatformAuditEntry } from '../types';

function LicenseHistory({ licenseId }: { licenseId: string }) {
  const { t } = useTranslation();
  const history = useAudit(20, 0, { target_type: 'license', target_id: licenseId });
  if (history.isError) {
    return <ErrorMessage error={history.error} onRetry={() => void history.refetch()} />;
  }
  return (
    <Card title={t('console:payment.history')}>
      <DataTable
        className="sm-table"
        tableStyle={{ minWidth: '40rem' }}
        value={history.data?.items ?? []}
        loading={history.isFetching}
        dataKey="id"
        emptyMessage={<EmptyState icon="pi pi-history" title={t('console:payment.historyEmpty')} />}
      >
        <Column
          header={t('console:audit.date')}
          body={(e: PlatformAuditEntry) => formatDateTime(e.occurred_at, 'fr')}
        />
        <Column field="actor_label" header={t('console:audit.actor')} />
        <Column field="action" header={t('console:audit.action')} bodyClassName="sm-nowrap" />
        <Column
          header={t('console:audit.changes')}
          body={(e: PlatformAuditEntry) => <AuditChanges entry={e} />}
        />
        <Column field="reason" header={t('console:audit.reason')} />
      </DataTable>
    </Card>
  );
}

/** Postes de l'abonnement du site (actifs et libérés) ; libération par TechNova (support). */
function LicensePostes({ license: l }: { license: ConsoleLicense }) {
  const { t } = useTranslation();
  const activations = useSubscriptionActivations(l.subscription_id);
  const [releasing, setReleasing] = useState<ConsoleActivation | null>(null);
  if (activations.isError) {
    return <ErrorMessage error={activations.error} onRetry={() => void activations.refetch()} />;
  }
  return (
    <Card
      title={t('console:license.postes.title', {
        used: l.activations_used,
        max: l.max_activations,
      })}
      data-testid="license-postes"
    >
      <DataTable
        className="sm-table"
        tableStyle={{ minWidth: '48rem' }}
        value={activations.data?.items ?? []}
        loading={activations.isFetching}
        dataKey="id"
        emptyMessage={<EmptyState icon="pi pi-desktop" title={t('console:license.postes.empty')} />}
      >
        <Column field="label" header={t('console:license.postes.label')} />
        <Column
          header={t('console:license.postes.installation')}
          body={(a: ConsoleActivation) => <code>{a.installation_id.slice(0, 8)}…</code>}
        />
        <Column
          header={t('console:license.postes.version')}
          body={(a: ConsoleActivation) => a.client_version ?? '—'}
        />
        <Column
          header={t('console:license.postes.lastSeen')}
          body={(a: ConsoleActivation) => formatDateTime(a.last_seen_at, 'fr')}
        />
        <Column
          header={t('console:license.postes.status')}
          body={(a: ConsoleActivation) => (
            <StatusBadge
              tone={a.status === 'ACTIVE' ? 'success' : 'neutral'}
              label={t(`console:license.postes.statuses.${a.status}`)}
            />
          )}
        />
        <Column
          header=""
          body={(a: ConsoleActivation) =>
            a.status === 'ACTIVE' ? (
              <Button
                icon="pi pi-power-off"
                text
                severity="danger"
                label={t('console:license.postes.release')}
                aria-label={`${t('console:license.postes.release')} ${a.label}`}
                onClick={() => setReleasing(a)}
              />
            ) : (
              <small className="sm-muted">{a.release_reason}</small>
            )
          }
        />
      </DataTable>
      {releasing && (
        <ReleaseActivationDialog activation={releasing} onClose={() => setReleasing(null)} />
      )}
    </Card>
  );
}

/** Contenu figé de la licence : modules, fonctionnalités et limites signés. */
function LicenseContent({ license: l }: { license: ConsoleLicense }) {
  const { t } = useTranslation();
  return (
    <Card title={t('console:license.content')}>
      <Details
        items={[
          [t('console:license.modules'), l.modules.join(', ') || '—'],
          [t('console:license.features'), l.features.join(', ') || '—'],
          ...Object.entries(l.limits).map(
            ([code, value]) =>
              [
                t(`console:limits.${code}`, { defaultValue: code }),
                value === null ? t('console:tenant.unlimited') : String(value),
              ] as [string, string],
          ),
          [t('console:license.keyId'), <code>{l.key_id}</code>],
          [t('console:license.payloadHash'), <code>{l.payload_sha256}</code>],
        ]}
      />
    </Card>
  );
}

export function LicenseDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id = '' } = useParams();
  const license = useLicense(id);
  const [kind, setKind] = useState<'revoke' | 'reissue' | null>(null);
  const [downloadError, setDownloadError] = useState<unknown>(null);

  if (license.isLoading) return <LoadingState />;
  if (license.isError || !license.data) {
    return <ErrorMessage error={license.error} onRetry={() => void license.refetch()} />;
  }
  const l = license.data;
  const revoked = l.status === 'REVOKED';
  const canReissue = !l.superseded_by_id && l.state !== 'EXPIRED';
  return (
    <>
      <PageHeader
        title={t('console:license.title', { number: l.license_number })}
        breadcrumbs={[
          { label: t('console:licenses.title'), to: `${CONSOLE_BASE}/licenses` },
          { label: l.license_number },
        ]}
        actions={
          <>
            <span data-testid="license-state">
              <LicenseStateBadge state={l.state} />
            </span>
            {!revoked && (
              <Button
                icon="pi pi-download"
                label={t('console:license.download')}
                outlined
                onClick={() => {
                  setDownloadError(null);
                  downloadLicense(l).catch(setDownloadError);
                }}
              />
            )}
          </>
        }
      />
      {downloadError !== null && (
        <Message severity="error" text={translateError(t, downloadError)} />
      )}
      <div className="sm-dashboard-grid">
        <Card title={t('console:license.details')}>
          <Details
            items={[
              [
                t('console:payment.company'),
                <Link to={`${CONSOLE_BASE}/tenants/${l.tenant_id}`}>{l.tenant_name}</Link>,
              ],
              [t('console:payment.site'), `${l.site_name} (${l.site_code})`],
              [t('console:payment.plan'), l.plan_code],
              [t('console:tenant.billingPeriod'), t(`billingPeriod.${l.billing_period}`)],
              [
                t('console:license.validity'),
                t('console:license.validityValue', {
                  start: paymentDay(l.valid_from),
                  end: paymentDay(l.valid_until),
                }),
                'license-validity',
              ],
              [t('console:license.timezone'), l.timezone],
              [
                t('console:license.maxActivations'),
                String(l.max_activations),
                'license-activations',
              ],
              [t('console:license.version'), String(l.license_version)],
              [
                t('console:license.payment'),
                <Link to={`${CONSOLE_BASE}/payments/${l.payment_id}`}>
                  {t('console:license.openPayment')}
                </Link>,
              ],
              [t('console:license.issuedAt'), formatDateTime(l.issued_at, 'fr')],
              [t('console:license.issuedBy'), l.issued_by_email],
              ...(l.supersedes_id
                ? [
                    [
                      t('console:license.supersedes'),
                      <Link to={`${CONSOLE_BASE}/licenses/${l.supersedes_id}`}>
                        {t('console:license.previous')}
                      </Link>,
                    ] as [string, ReactNode],
                  ]
                : []),
              ...(l.superseded_by_id
                ? [
                    [
                      t('console:license.supersededBy'),
                      <Link to={`${CONSOLE_BASE}/licenses/${l.superseded_by_id}`}>
                        {t('console:license.next')}
                      </Link>,
                    ] as [string, ReactNode],
                  ]
                : []),
            ]}
          />
        </Card>
        <Card title={t('console:license.lifecycle')}>
          {revoked ? (
            <Details
              items={[
                [
                  t('console:license.state'),
                  <StatusBadge tone="danger" label={t('licenseState.REVOKED')} />,
                ],
                [
                  t('console:license.revokedAt'),
                  l.revoked_at ? formatDateTime(l.revoked_at, 'fr') : null,
                ],
                [t('console:license.revokedBy'), l.revoked_by_email],
                [
                  t('console:license.revocationReason'),
                  l.revocation_reason,
                  'license-revocation-reason',
                ],
              ]}
            />
          ) : (
            <p className="sm-help">{t('console:license.issuedHelp')}</p>
          )}
          <p className="sm-help">{t('console:license.immutableHelp')}</p>
          <div className="sm-quick-actions" data-testid="license-actions">
            {!revoked && (
              <Button
                icon="pi pi-ban"
                severity="danger"
                outlined
                label={t('console:license.revokeAction')}
                onClick={() => setKind('revoke')}
              />
            )}
            {canReissue && (
              <Button
                icon="pi pi-replay"
                label={t('console:license.reissueAction')}
                onClick={() => setKind('reissue')}
              />
            )}
          </div>
        </Card>
      </div>
      <LicensePostes license={l} />
      <LicenseContent license={l} />
      <LicenseHistory licenseId={l.id} />
      {kind && (
        <LicenseActionDialog
          license={l}
          kind={kind}
          onClose={() => setKind(null)}
          onDone={(result) => {
            setKind(null);
            if (result.id !== l.id) navigate(`${CONSOLE_BASE}/licenses/${result.id}`);
          }}
        />
      )}
    </>
  );
}
