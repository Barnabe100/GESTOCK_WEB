import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { MultiSelect } from 'primereact/multiselect';
import { Password } from 'primereact/password';
import { Tag } from 'primereact/tag';
import { useState } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { useSites } from '@/modules/organization/api';
import { translateError } from '@/shared/lib/errors';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';

import { useMembers, useRoles, useSaveMember, type Member } from './api';

const schema = z.object({
  email: z.string().trim().email(),
  full_name: z.string().trim().min(1).max(150),
  password: z.string().max(256),
  role_ids: z.array(z.string()),
  site_ids: z.array(z.string()),
  all_sites: z.boolean(),
  status: z.enum(['active', 'suspended']),
});
type FormValues = z.infer<typeof schema>;

function MemberDialog({ member, onClose }: { member: Member | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const roles = useRoles();
  const sites = useSites();
  const save = useSaveMember();
  const editing = member !== null;
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      email: member?.email ?? '',
      full_name: member?.full_name ?? '',
      password: '',
      // Les rôles limités à un site (API) sont conservés tels quels à l'édition.
      role_ids: member?.roles.filter((r) => !r.site_id).map((r) => r.role_id) ?? [],
      site_ids: member?.site_ids ?? [],
      all_sites: member?.all_sites ?? false,
      status: member?.status ?? 'active',
    },
  });
  const errors = form.formState.errors;
  const allSites = useWatch({ control: form.control, name: 'all_sites' });

  const onSubmit = form.handleSubmit((values) => {
    const tenantWide = values.role_ids.map((role_id) => ({ role_id, site_id: null }));
    const siteScoped = member?.roles.filter((r) => r.site_id) ?? [];
    const site_ids = values.all_sites ? [] : values.site_ids;
    const mutation = editing
      ? {
          id: member.id,
          input: {
            roles: [...tenantWide, ...siteScoped],
            site_ids,
            all_sites: values.all_sites,
            status: values.status,
          },
        }
      : {
          input: {
            email: values.email,
            full_name: values.full_name,
            password: values.password || undefined,
            roles: tenantWide.map(({ role_id }) => ({ role_id })),
            site_ids,
            all_sites: values.all_sites,
          },
        };
    save.mutate(mutation, {
      onSuccess: () => {
        toast.success(t(editing ? 'members.updated' : 'members.created'));
        onClose();
      },
      onError: (error) => toast.error(translateError(t, error)),
    });
  });

  return (
    <Dialog
      header={t(editing ? 'members.edit' : 'members.new')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <FormField
          id="member-email"
          label={t('members.email')}
          error={errors.email && t('validation.email')}
        >
          <InputText
            id="member-email"
            type="email"
            {...form.register('email')}
            disabled={editing}
            autoFocus
          />
        </FormField>
        <FormField
          id="member-name"
          label={t('members.name')}
          error={errors.full_name && t('validation.required')}
        >
          <InputText id="member-name" {...form.register('full_name')} disabled={editing} />
        </FormField>
        {!editing && (
          <FormField
            id="member-password"
            label={t('members.temporaryPassword')}
            help={t('members.temporaryPasswordHelp')}
          >
            <Controller
              control={form.control}
              name="password"
              render={({ field }) => (
                <Password
                  inputId="member-password"
                  value={field.value}
                  onChange={(e) => field.onChange(e.target.value)}
                  feedback={false}
                  toggleMask
                  autoComplete="new-password"
                />
              )}
            />
          </FormField>
        )}
        <FormField id="member-roles" label={t('members.roles')}>
          <Controller
            control={form.control}
            name="role_ids"
            render={({ field }) => (
              <MultiSelect
                inputId="member-roles"
                value={field.value}
                onChange={(e) => field.onChange(e.value)}
                options={(roles.data ?? []).map((r) => ({ value: r.id, label: r.name }))}
                display="chip"
              />
            )}
          />
        </FormField>
        <div className="sm-checkbox">
          <Controller
            control={form.control}
            name="all_sites"
            render={({ field }) => (
              <Checkbox
                inputId="member-all-sites"
                checked={field.value}
                onChange={(e) => field.onChange(Boolean(e.checked))}
              />
            )}
          />
          <label htmlFor="member-all-sites">{t('members.allSites')}</label>
        </div>
        {!allSites && (
          <FormField id="member-sites" label={t('members.sites')}>
            <Controller
              control={form.control}
              name="site_ids"
              render={({ field }) => (
                <MultiSelect
                  inputId="member-sites"
                  value={field.value}
                  onChange={(e) => field.onChange(e.value)}
                  options={(sites.data ?? []).map((s) => ({ value: s.id, label: s.name }))}
                  display="chip"
                />
              )}
            />
          </FormField>
        )}
        {editing && (
          <FormField id="member-status" label={t('members.status')}>
            <Controller
              control={form.control}
              name="status"
              render={({ field }) => (
                <Dropdown
                  inputId="member-status"
                  value={field.value}
                  onChange={(e) => field.onChange(e.value)}
                  options={(['active', 'suspended'] as const).map((s) => ({
                    value: s,
                    label: t(`members.statuses.${s}`),
                  }))}
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

export default function MembersPage() {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const members = useMembers();
  const roles = useRoles();
  const sites = useSites();
  const [editing, setEditing] = useState<Member | null | undefined>(undefined);
  const canManage = can('users.member.manage');
  const roleNames = new Map((roles.data ?? []).map((r) => [r.id, r.name]));
  const siteNames = new Map((sites.data ?? []).map((s) => [s.id, s.name]));

  return (
    <>
      <PageHeader
        title={t('members.title')}
        actions={
          canManage && (
            <Button
              icon="pi pi-user-plus"
              label={t('members.new')}
              onClick={() => setEditing(null)}
            />
          )
        }
      />
      {members.isError ? (
        <ErrorMessage error={members.error} onRetry={() => void members.refetch()} />
      ) : (
        <DataTable value={members.data ?? []} loading={members.isPending} dataKey="id">
          <Column
            header={t('members.name')}
            body={(m: Member) => (
              <div>
                <div>{m.full_name}</div>
                <small className="sm-muted">{m.email}</small>
              </div>
            )}
          />
          <Column
            header={t('members.roles')}
            body={(m: Member) =>
              m.is_owner ? (
                <Tag value={t('auth.owner')} />
              ) : (
                m.roles
                  .map(
                    (r) =>
                      roleNames.get(r.role_id) +
                      (r.site_id ? ` (${siteNames.get(r.site_id) ?? '…'})` : ''),
                  )
                  .join(', ') || t('common.none')
              )
            }
          />
          <Column
            header={t('members.sites')}
            body={(m: Member) =>
              m.is_owner || m.all_sites
                ? t('common.all')
                : m.site_ids.map((id) => siteNames.get(id) ?? '…').join(', ') || t('common.none')
            }
          />
          <Column
            header={t('members.status')}
            body={(m: Member) => (
              <div className="sm-tags">
                <Tag
                  severity={m.status === 'active' ? 'success' : 'secondary'}
                  value={t(`members.statuses.${m.status}`)}
                />
                {m.must_change_password && (
                  <Tag severity="warning" value={t('members.mustChange')} />
                )}
              </div>
            )}
          />
          {canManage && (
            <Column
              body={(m: Member) =>
                !m.is_owner && m.user_id !== capabilities.user.id ? (
                  <Button
                    icon="pi pi-pencil"
                    text
                    aria-label={t('actions.edit')}
                    onClick={() => setEditing(m)}
                  />
                ) : null
              }
            />
          )}
        </DataTable>
      )}
      {editing !== undefined && (
        <MemberDialog member={editing} onClose={() => setEditing(undefined)} />
      )}
    </>
  );
}
