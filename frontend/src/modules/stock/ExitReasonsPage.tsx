import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
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
import { StatusFilter } from '@/shared/ui/StatusFilter';
import { ActiveBadge, StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';
import { FilterBar } from '@/shared/ui/FilterBar';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { RowActions } from '@/shared/ui/RowActions';
import { confirmAction } from '@/shared/ui/confirm';

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
          required
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

  // Désactivation : action sensible, confirmée ; réactivation directe.
  const toggle = (r: ExitReason) => {
    const run = () =>
      setActive.mutate(
        { id: r.id, active: !r.is_active },
        {
          onSuccess: () => toast.success(t('exitReasons.statusChanged')),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!r.is_active) return run();
    confirmAction(t, {
      header: t('exitReasons.deactivateTitle'),
      message: t('exitReasons.deactivateConfirm', { name: r.label }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: run,
    });
  };

  return (
    <>
      <PageHeader
        title={t('exitReasons.title')}
        description={t('exitReasons.subtitle')}
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
      <FilterBar onReset={() => setStatus('all')} active={status !== 'all'}>
        <StatusFilter value={status} onChange={setStatus} />
      </FilterBar>
      {reasons.isError ? (
        <ErrorMessage error={reasons.error} onRetry={() => void reasons.refetch()} />
      ) : (
        <DataTable
          className="sm-table"
          value={reasons.data?.items ?? []}
          loading={reasons.isFetching}
          dataKey="id"
          rowHover
          tableStyle={{ minWidth: '40rem' }}
          emptyMessage={<ListEmpty filtered={status !== 'all'} title={t('exitReasons.empty')} />}
        >
          <Column
            header={t('exitReasons.label')}
            body={(r: ExitReason) => (
              <div className="sm-tags">
                <span>{r.label}</span>
                {r.is_system && <StatusBadge tone="info" label={t('exitReasons.system')} />}
              </div>
            )}
          />
          <Column field="description" header={t('exitReasons.description')} />
          <Column
            header={t('categories.status')}
            body={(r: ExitReason) => <ActiveBadge active={r.is_active} />}
          />
          {canManage && (
            <Column
              header={t('common.actions')}
              body={(r: ExitReason) => (
                <RowActions
                  actions={[
                    {
                      key: 'edit',
                      label: t('actions.edit'),
                      icon: 'pi pi-pencil',
                      onClick: () => setEditing(r),
                      hidden: r.is_system,
                    },
                    {
                      key: 'status',
                      label: t(r.is_active ? 'actions.deactivate' : 'actions.activate'),
                      icon: r.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                      danger: r.is_active,
                      onClick: () => toggle(r),
                    },
                  ]}
                />
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
