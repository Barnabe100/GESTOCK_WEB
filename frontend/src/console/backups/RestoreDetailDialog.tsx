import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Message } from 'primereact/message';
import { ProgressBar } from 'primereact/progressbar';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useToast } from '@/shared/ui/toast';

import { useRestore, useRestoreDecision } from '../queries';
import type { RestoreCheck, RestoreDecision, RestoreDetail } from '../types';
import { ReasonDialog, RestoreStatusBadge, bytes, when, whenUtc } from './shared';

/** Contrôles d'un rapport (copie isolée, matérialisation PITR, application). */
function checksOf(report: unknown): RestoreCheck[] {
  if (!report || typeof report !== 'object') return [];
  const checks = (report as { checks?: unknown }).checks;
  return Array.isArray(checks)
    ? checks.filter((c): c is RestoreCheck => typeof c === 'object' && c !== null && 'name' in c)
    : [];
}

function Report({ title, report }: { title: string; report: unknown }) {
  const { t } = useTranslation();
  const checks = checksOf(report);
  if (!report) return null;
  const message = (report as { message?: string }).message;
  return (
    <section className="sm-report" data-testid={`report-${title}`}>
      <h4>{t(`console:bk.reports.${title}`)}</h4>
      {message && <p className="sm-muted">{message}</p>}
      {checks.length > 0 && (
        <ul className="sm-checks">
          {checks.map((c) => (
            <li key={c.name} className={c.ok ? 'sm-ok' : 'sm-danger'}>
              <i className={c.ok ? 'pi pi-check' : 'pi pi-times'} aria-hidden />{' '}
              <span className="sm-strong">
                {t(`console:bk.checks.${c.name}`, { defaultValue: c.name })}
              </span>
              {c.detail ? ` — ${c.detail}` : ''}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Restauration : état, rapport de la vérification sur copie isolée et décisions. L'approbation
 * est réservée à un administrateur DIFFÉRENT du demandeur (contrôlé par le serveur et la base) ;
 * la restauration globale, approuvée, s'applique par la procédure manuelle documentée.
 */
export function RestoreDetailDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const [current, setCurrent] = useState(id);
  return <RestoreDetailContent key={current} id={current} onOpen={setCurrent} onClose={onClose} />;
}

function RestoreDetailContent({
  id,
  onOpen,
  onClose,
}: {
  id: string;
  onOpen: (id: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const restore = useRestore(id);
  const decide = useRestoreDecision();
  const [decision, setDecision] = useState<RestoreDecision | null>(null);
  const data: RestoreDetail | undefined = restore.data;
  const v = data?.verification ?? {};
  return (
    <Dialog
      header={t('console:bk.restoreTitle')}
      visible
      onHide={onClose}
      className="sm-dialog"
      style={{ width: 'min(52rem, 96vw)' }}
      footer={
        data && (
          <>
            {data.actions.cancel && (
              <Button
                label={t('console:bk.decisions.cancel')}
                text
                severity="danger"
                onClick={() => setDecision('cancel')}
                data-testid="restore-cancel"
              />
            )}
            {data.actions.reject && (
              <Button
                label={t('console:bk.decisions.reject')}
                outlined
                severity="danger"
                onClick={() => setDecision('reject')}
                data-testid="restore-reject"
              />
            )}
            {data.actions.rollback && (
              <Button
                label={t('console:bk.decisions.rollback')}
                outlined
                icon="pi pi-undo"
                onClick={() => setDecision('rollback')}
                data-testid="restore-rollback"
              />
            )}
            {data.status === 'verified' && (
              <Button
                label={t('console:bk.decisions.approve')}
                icon="pi pi-check"
                disabled={!data.actions.approve}
                onClick={() => setDecision('approve')}
                data-testid="restore-approve"
              />
            )}
            <Button label={t('console:bk.close')} text onClick={onClose} />
          </>
        )
      }
    >
      {!data ? (
        <ProgressBar mode="indeterminate" style={{ height: '0.5rem' }} />
      ) : (
        <>
          <dl className="sm-details" data-testid="restore-detail">
            <div>
              <dt>{t('console:bk.status')}</dt>
              <dd>
                <RestoreStatusBadge status={data.status} /> {data.progress_message ?? ''}
              </dd>
            </div>
            <div>
              <dt>{t('console:bk.target')}</dt>
              <dd>
                {data.scope === 'platform' ? t('console:bk.platformRestore') : data.tenant_name}
                {' — '}
                {data.mode === 'pitr'
                  ? t('console:bk.pitrAt', { when: whenUtc(data.target_time) })
                  : t('console:bk.fromBackup', { when: when(data.source_item?.created_at) })}
              </dd>
            </div>
            <div>
              <dt>{t('console:bk.requestedBy')}</dt>
              <dd>
                {data.requested_label} · {when(data.requested_at)} — « {data.reason} »
              </dd>
            </div>
            {data.approved_label && (
              <div>
                <dt>{t('console:bk.approvedBy')}</dt>
                <dd>
                  {data.approved_label} · {when(data.approved_at)} — « {data.approval_reason} »
                </dd>
              </div>
            )}
            {data.decision_expires_at && data.status === 'verified' && (
              <div>
                <dt>{t('console:bk.decisionBefore')}</dt>
                <dd>{when(data.decision_expires_at)}</dd>
              </div>
            )}
            {data.rollback_of && (
              <div>
                <dt>{t('console:bk.rollbackOf')}</dt>
                <dd>
                  <Button
                    link
                    className="p-0"
                    label={data.rollback_of.slice(0, 8)}
                    onClick={() => data.rollback_of && onOpen(data.rollback_of)}
                  />
                </dd>
              </div>
            )}
            {data.applied_at && (
              <div>
                <dt>{t('console:bk.appliedAt')}</dt>
                <dd>
                  {when(data.applied_at)}
                  {data.rollback_deadline &&
                    ` — ${t('console:bk.rollbackUntil', { when: when(data.rollback_deadline) })}`}
                </dd>
              </div>
            )}
            {data.source_item && (
              <div>
                <dt>{t('console:bk.source')}</dt>
                <dd>
                  {t('console:bk.tables', { count: data.source_item.table_count ?? 0 })} ·{' '}
                  {t('console:bk.rows', { count: data.source_item.row_count ?? 0 })} ·{' '}
                  {bytes(data.source_item.size_bytes)} ·{' '}
                  <code title={data.source_item.manifest_sha256 ?? ''}>
                    {(data.source_item.manifest_sha256 ?? '').slice(0, 12)}
                  </code>
                </dd>
              </div>
            )}
          </dl>
          {['requested', 'verifying', 'applying'].includes(data.status) && (
            <ProgressBar value={data.progress_percent} />
          )}
          {data.actions.approve_blocked === 'same_admin' && data.status === 'verified' && (
            <Message severity="info" text={t('console:bk.sameAdmin')} className="sm-block" />
          )}
          {data.manual_application && (
            <Message
              severity="warn"
              text={t('console:bk.manualApplication')}
              className="sm-block"
            />
          )}
          {data.error && <Message severity="error" text={data.error} className="sm-block" />}
          <Report title="materialization" report={v.materialization} />
          <Report title="copy" report={v.copy} />
          <Report title="application" report={v.application} />
        </>
      )}
      {decision && data && (
        <ReasonDialog
          header={t(`console:bk.decisions.${decision}`)}
          acceptLabel={t(`console:bk.decisions.${decision}`)}
          danger={decision !== 'approve'}
          requireConfirmation={decision === 'approve' || decision === 'rollback'}
          warning={t(`console:bk.decisionWarning.${decision}`)}
          pending={decide.isPending}
          error={decide.error}
          onClose={() => setDecision(null)}
          onAccept={(reason) =>
            decide.mutate(
              { id: data.id, decision, reason },
              {
                onSuccess: (result) => {
                  toast.success(t(`console:bk.decisionDone.${decision}`));
                  setDecision(null);
                  if (decision === 'rollback') onOpen(result.id);
                },
              },
            )
          }
        />
      )}
    </Dialog>
  );
}
