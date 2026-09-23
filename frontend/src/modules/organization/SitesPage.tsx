import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputSwitch } from 'primereact/inputswitch';
import { InputText } from 'primereact/inputtext';
import { Tag } from 'primereact/tag';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import type { SiteKind } from '@/core/api/types';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';

import { useSaveSite, useSites, type Site } from './api';

const KINDS: SiteKind[] = ['store', 'warehouse', 'restaurant', 'other'];

const schema = z.object({
  name: z.string().trim().min(1).max(150),
  code: z
    .string()
    .trim()
    .regex(/^[A-Za-z0-9_-]{1,30}$/),
  kind: z.enum(['store', 'warehouse', 'restaurant', 'other']),
  address: z.string().max(255),
  phone: z.string().max(50),
  is_active: z.boolean(),
});
type FormValues = z.infer<typeof schema>;

function SiteDialog({ site, onClose }: { site: Site | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const save = useSaveSite();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: site?.name ?? '',
      code: site?.code ?? '',
      kind: site?.kind ?? 'store',
      address: site?.address ?? '',
      phone: site?.phone ?? '',
      is_active: site?.is_active ?? true,
    },
  });
  const errors = form.formState.errors;

  const onSubmit = form.handleSubmit(({ is_active, ...values }) => {
    const input = {
      ...values,
      address: values.address || null,
      phone: values.phone || null,
      ...(site ? { is_active } : {}),
    };
    save.mutate(
      { id: site?.id, input },
      {
        onSuccess: () => {
          toast.success(t(site ? 'sites.updated' : 'sites.created'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  });

  return (
    <Dialog
      header={t(site ? 'sites.edit' : 'sites.new')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <FormField
          id="site-name"
          label={t('sites.name')}
          error={errors.name && t('validation.required')}
        >
          <InputText id="site-name" {...form.register('name')} autoFocus />
        </FormField>
        <FormField
          id="site-code"
          label={t('sites.code')}
          error={errors.code && t('validation.invalid')}
        >
          <InputText id="site-code" {...form.register('code')} />
        </FormField>
        <FormField id="site-kind" label={t('sites.kind')}>
          <Controller
            control={form.control}
            name="kind"
            render={({ field }) => (
              <Dropdown
                inputId="site-kind"
                value={field.value}
                onChange={(e) => field.onChange(e.value)}
                options={KINDS.map((k) => ({ value: k, label: t(`sites.kinds.${k}`) }))}
              />
            )}
          />
        </FormField>
        <FormField id="site-address" label={t('sites.address')}>
          <InputText id="site-address" {...form.register('address')} />
        </FormField>
        <FormField id="site-phone" label={t('sites.phone')}>
          <InputText id="site-phone" {...form.register('phone')} />
        </FormField>
        {site && (
          <FormField id="site-active" label={t('common.active')}>
            <Controller
              control={form.control}
              name="is_active"
              render={({ field }) => (
                <InputSwitch
                  inputId="site-active"
                  checked={field.value}
                  onChange={(e) => field.onChange(e.value)}
                />
              )}
            />
          </FormField>
        )}
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}

export default function SitesPage() {
  const { t } = useTranslation();
  const { can } = useCapabilities();
  const sites = useSites();
  const [editing, setEditing] = useState<Site | null | undefined>(undefined);
  const canManage = can('organization.site.manage');

  return (
    <>
      <PageHeader
        title={t('sites.title')}
        actions={
          canManage && (
            <Button icon="pi pi-plus" label={t('sites.new')} onClick={() => setEditing(null)} />
          )
        }
      />
      {sites.isError ? (
        <ErrorMessage error={sites.error} onRetry={() => void sites.refetch()} />
      ) : (
        <DataTable
          value={sites.data ?? []}
          loading={sites.isPending}
          dataKey="id"
          emptyMessage={t('common.noData')}
        >
          <Column field="name" header={t('sites.name')} />
          <Column field="code" header={t('sites.code')} />
          <Column header={t('sites.kind')} body={(s: Site) => t(`sites.kinds.${s.kind}`)} />
          <Column
            header={t('sites.status')}
            body={(s: Site) => (
              <Tag
                severity={s.is_active ? 'success' : 'secondary'}
                value={t(s.is_active ? 'common.active' : 'common.inactive')}
              />
            )}
          />
          {canManage && (
            <Column
              body={(s: Site) => (
                <Button
                  icon="pi pi-pencil"
                  text
                  aria-label={t('actions.edit')}
                  onClick={() => setEditing(s)}
                />
              )}
            />
          )}
        </DataTable>
      )}
      {editing !== undefined && <SiteDialog site={editing} onClose={() => setEditing(undefined)} />}
    </>
  );
}
