import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { ProgressBar } from 'primereact/progressbar';
import { RadioButton } from 'primereact/radiobutton';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';

import { useBackupRun, useRequestBackup } from '../queries';
import type { BackupCategory } from '../types';
import { RunStatusBadge, TenantPicker, bytes } from './shared';

const KINDS: BackupCategory[] = ['logical', 'physical', 'verification', 'restore_test'];

/**
 * « Sauvegarder » : sauvegarde immédiate de TOUTES les entreprises ou d'UNE seule. L'agent
 * l'exécute (au plus une minute d'attente) ; la progression, le résultat et les erreurs sont
 * relus sur le serveur jusqu'à la fin. Une sauvegarde d'une entreprise est toujours logique ;
 * la sauvegarde physique (cluster complet, WAL) concerne la plateforme.
 */
export function BackupNowDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const request = useRequestBackup();
  const [scope, setScope] = useState<'platform' | 'tenant'>('platform');
  const [kind, setKind] = useState<BackupCategory>('logical');
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [runId, setRunId] = useState<string | null>(null);
  const run = useBackupRun(runId);
  const data = run.data;
  const finished = data && ['succeeded', 'failed', 'cancelled'].includes(data.status);
  const effectiveKind = scope === 'tenant' ? 'logical' : kind;

  const submit = () =>
    request.mutate(
      {
        kind: effectiveKind,
        scope,
        tenant_id: scope === 'tenant' ? tenantId : null,
        reason: reason.trim() || null,
      },
      { onSuccess: (created) => setRunId(created.id) },
    );

  return (
    <Dialog
      header={t('console:bk.now.title')}
      visible
      onHide={onClose}
      className="sm-dialog"
      style={{ width: 'min(40rem, 95vw)' }}
      footer={
        runId ? (
          <Button label={t('console:bk.close')} onClick={onClose} data-testid="backup-now-close" />
        ) : (
          <>
            <Button label={t('actions.cancel')} text severity="secondary" onClick={onClose} />
            <Button
              label={t('console:bk.now.start')}
              icon="pi pi-play"
              loading={request.isPending}
              disabled={scope === 'tenant' && !tenantId}
              onClick={submit}
              data-testid="backup-now-start"
            />
          </>
        )
      }
    >
      {!runId && (
        <>
          <fieldset className="sm-fieldset">
            <legend>{t('console:bk.now.scope')}</legend>
            {(['platform', 'tenant'] as const).map((value) => (
              <div key={value} className="sm-checkbox-row">
                <RadioButton
                  inputId={`scope-${value}`}
                  checked={scope === value}
                  onChange={() => setScope(value)}
                  data-testid={`scope-${value}`}
                />
                <label htmlFor={`scope-${value}`}>{t(`console:bk.now.scopes.${value}`)}</label>
              </div>
            ))}
          </fieldset>
          {scope === 'tenant' ? (
            <FormField id="backup-tenant" label={t('console:bk.tenant')} required>
              <TenantPicker id="backup-tenant" value={tenantId} onChange={setTenantId} />
            </FormField>
          ) : (
            <FormField
              id="backup-kind"
              label={t('console:bk.kind')}
              help={t(`console:bk.kindHelp.${kind}`)}
            >
              <Dropdown
                inputId="backup-kind"
                data-testid="backup-kind"
                value={kind}
                options={KINDS.map((k) => ({ label: t(`console:bk.kinds.${k}`), value: k }))}
                onChange={(e) => setKind(e.value as BackupCategory)}
              />
            </FormField>
          )}
          {scope === 'tenant' && <p className="sm-help">{t('console:bk.now.tenantLogical')}</p>}
          <FormField id="backup-reason" label={t('console:bk.reasonOptional')}>
            <InputText
              id="backup-reason"
              value={reason}
              maxLength={500}
              onChange={(e) => setReason(e.target.value)}
            />
          </FormField>
          {request.isError && <Message severity="error" text={translateError(t, request.error)} />}
        </>
      )}
      {runId && (
        <div data-testid="backup-progress">
          {!data ? (
            <ProgressBar mode="indeterminate" style={{ height: '0.5rem' }} />
          ) : (
            <>
              <p>
                <RunStatusBadge status={data.status} /> {t(`console:bk.kinds.${data.kind}`)}
                {data.tenant_name ? ` — ${data.tenant_name}` : ` — ${t('console:bk.allTenants')}`}
              </p>
              {data.status === 'queued' && <p className="sm-help">{t('console:bk.now.queued')}</p>}
              <ProgressBar
                value={data.progress_percent}
                mode={data.status === 'queued' ? 'indeterminate' : 'determinate'}
              />
              <p className="sm-muted">{data.progress_message ?? ''}</p>
              {data.items_total > 0 && (
                <p data-testid="backup-items-count">
                  {t('console:bk.itemsCount', {
                    ok: data.items_succeeded,
                    failed: data.items_failed,
                    total: data.items_total,
                  })}
                </p>
              )}
              {data.status === 'succeeded' && (
                <Message severity="success" text={t('console:bk.now.succeeded')} />
              )}
              {data.status === 'failed' && (
                <Message
                  severity="error"
                  text={`${t('console:bk.now.failed')} ${data.error ?? ''}`}
                  data-testid="backup-error"
                />
              )}
              {finished && data.items.length > 0 && (
                <ul className="sm-list" data-testid="backup-items">
                  {data.items.map((item) => (
                    <li key={item.id}>
                      {item.tenant_name} — {t(`console:bk.itemStatus.${item.status}`)}
                      {item.status === 'succeeded' &&
                        ` · ${t('console:bk.rows', { count: item.row_count ?? 0 })} · ${bytes(item.size_bytes)}`}
                      {item.error && ` · ${item.error}`}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      )}
    </Dialog>
  );
}
