import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
import { Tag } from 'primereact/tag';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import type { StatusFilterValue } from '@/shared/lib/serverTable';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { ActiveTag, StatusFilter } from '@/shared/ui/StatusFilter';
import { useToast } from '@/shared/ui/toast';

import { useExitReasons, useSaveExitReason, useSetExitReasonActive, type ExitReason } from './api';

const schema = z.object({
  label: z.string().trim().min(1).max(150),
  description: z.string().max(500),
});
type FormValues = z.infer<typeof schema>;

function ReasonDialog({ reason, onClose }: { reason: ExitReason | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const save = useSaveExitReason();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { label: reason?.label ?? '', description: reason?.description ?? '' },
  });

  const onSubmit = form.handleSubmit((values) =>
    save.mutate(
      { id: reason?.id, input: { ...values, description: values.description || null } },
      {
        onSuccess: () => {
          toast.success(t(reason ? 'exitReasons.updated' : 'exitReasons.created'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    ),
  );

  return (
    <Dialog
      header={t(reason ? 'exitReasons.edit' : 'exitReasons.new')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <FormField
          id="reason-label"
          label={t('exitReasons.label')}
          error={form.formState.errors.label && t('validation.required')}
        >
          <InputText id="reason-label" {...form.register('label')} autoFocus />
        </FormField>
        <FormField id="reason-description" label={t('exitReasons.description')}>
          <InputText id="reason-description" {...form.register('description')} />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}

export default function ExitReasonsPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const [editing, setEditing] = useState<ExitReason | null | undefined>(undefined);
  const reasons = useExitReasons(`limit=200&sort=label&status=${status}`);
  const setActive = useSetExitReasonActive();
  const canManage = can('stock.reason.manage');

  const toggle = (r: ExitReason) =>
    setActive.mutate(
      { id: r.id, active: !r.is_active },
      {
        onSuccess: () => toast.success(t('exitReasons.statusChanged')),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );

  return (
    <>
      <PageHeader
        title={t('exitReasons.title')}
        actions={
          canManage && (
            <Button
              icon="pi pi-plus"
              label={t('exitReasons.new')}
              onClick={() => setEditing(null)}
            />
          )
        }
      />
      <div className="sm-toolbar">
        <StatusFilter value={status} onChange={setStatus} />
      </div>
      {reasons.isError ? (
        <ErrorMessage error={reasons.error} onRetry={() => void reasons.refetch()} />
      ) : (
        <DataTable
          value={reasons.data?.items ?? []}
          loading={reasons.isFetching}
          dataKey="id"
          emptyMessage={t('common.noData')}
        >
          <Column
            header={t('exitReasons.label')}
            body={(r: ExitReason) => (
              <div className="sm-tags">
                <span>{r.label}</span>
                {r.is_system && <Tag severity="secondary" value={t('exitReasons.system')} />}
              </div>
            )}
          />
          <Column field="description" header={t('exitReasons.description')} />
          <Column
            header={t('categories.status')}
            body={(r: ExitReason) => <ActiveTag active={r.is_active} />}
          />
          {canManage && (
            <Column
              header={t('common.actions')}
              body={(r: ExitReason) => (
                <div className="sm-row-actions">
                  {!r.is_system && (
                    <Button
                      icon="pi pi-pencil"
                      text
                      aria-label={t('actions.edit')}
                      onClick={() => setEditing(r)}
                    />
                  )}
                  <Button
                    icon={r.is_active ? 'pi pi-ban' : 'pi pi-check'}
                    text
                    aria-label={t(r.is_active ? 'actions.deactivate' : 'actions.activate')}
                    onClick={() => toggle(r)}
                  />
                </div>
              )}
            />
          )}
        </DataTable>
      )}
      {editing !== undefined && (
        <ReasonDialog reason={editing} onClose={() => setEditing(undefined)} />
      )}
    </>
  );
}
