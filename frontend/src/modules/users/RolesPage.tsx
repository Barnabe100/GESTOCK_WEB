import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { TabPanel, TabView } from 'primereact/tabview';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { ApiError } from '@/core/api/client';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import type { StatusFilterValue } from '@/shared/lib/serverTable';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { StatusFilter } from '@/shared/ui/StatusFilter';
import { useToast } from '@/shared/ui/toast';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { confirmAction } from '@/shared/ui/confirm';
import { EmptyState } from '@/shared/ui/EmptyState';
import { RowActions } from '@/shared/ui/RowActions';

import {
  useCreateRoleFromTemplate,
  useDuplicateRole,
  usePermissions,
  useRoleMembers,
  useRoles,
  useRoleTemplates,
  useSaveRole,
  useSetRoleActive,
  type Role,
} from './api';
import { normalizeSearch } from './permissionGroups';
import { PermissionPicker } from './PermissionPicker';

const schema = z.object({
  name: z.string().trim().min(1).max(100),
  description: z.string().max(500),
  permissions: z.array(z.string()),
});
type FormValues = z.infer<typeof schema>;

function RoleTags({ role }: { role: Role }) {
  const { t } = useTranslation();
  return (
    <>
      {role.is_system && <StatusBadge tone="info" label={t('roles.system')} />}
      {role.protected && (
        <StatusBadge tone="warning" icon="pi pi-lock" label={t('roles.protected')} />
      )}
      {!role.is_active && <StatusBadge tone="neutral" label={t('common.inactive')} />}
    </>
  );
}

function RoleMembers({ role }: { role: Role }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const members = useRoleMembers(role.id, true);
  const siteNames = new Map(capabilities.sites.map((s) => [s.id, s.name]));
  if (members.isError) {
    return <ErrorMessage error={members.error} onRetry={() => void members.refetch()} />;
  }
  return (
    <DataTable
      value={members.data ?? []}
      loading={members.isPending}
      dataKey={(m: { membership_id: string; site_id: string | null }) =>
        `${m.membership_id}:${m.site_id ?? ''}`
      }
      emptyMessage={t('roles.noMembers')}
    >
      <Column field="full_name" header={t('members.name')} />
      <Column field="email" header={t('members.email')} />
      <Column
        header={t('roles.scope')}
        body={(m: { site_id: string | null }) =>
          m.site_id ? (siteNames.get(m.site_id) ?? '…') : t('roles.wholeTenant')
        }
      />
      <Column
        header={t('members.status')}
        body={(m: { status: 'active' | 'suspended' }) => t(`members.statuses.${m.status}`)}
      />
    </DataTable>
  );
}

/** Création, modification (rôle personnalisé) ou consultation (rôle de base, lecture seule). */
function RoleDialog({ role, onClose }: { role: Role | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const permissions = usePermissions();
  const save = useSaveRole();
  const readOnly = role !== null && (role.is_system || !can('users.role.manage'));
  const available = new Set((permissions.data ?? []).map((p) => p.code));
  // Permissions enregistrées d'un module sorti de l'offre : sans effet, retirées à l'enregistrement.
  const outOfOffer = permissions.data
    ? (role?.permission_codes ?? []).filter((c) => !available.has(c))
    : [];
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: role?.name ?? '',
      description: role?.description ?? '',
      permissions: role?.permission_codes ?? [],
    },
  });

  const onSubmit = form.handleSubmit((values) =>
    save.mutate(
      {
        id: role?.id,
        input: {
          name: values.name,
          description: values.description,
          permissions: values.permissions.filter((c) => available.has(c)),
        },
      },
      {
        onSuccess: () => {
          toast.success(t(role ? 'roles.updated' : 'roles.created'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    ),
  );

  const header = role
    ? readOnly
      ? role.name
      : `${t('roles.edit')} — ${role.name}`
    : t('roles.new');
  const details = (
    <form onSubmit={onSubmit} className="sm-form" noValidate>
      {role?.is_system && <Message severity="info" text={t('roles.systemReadOnly')} />}
      <div className="sm-form-grid">
        <FormField
          id="role-name"
          label={t('roles.name')}
          required
          error={form.formState.errors.name && t('validation.required')}
        >
          <InputText id="role-name" {...form.register('name')} disabled={readOnly} autoFocus />
        </FormField>
        <FormField id="role-description" label={t('roles.description')}>
          <InputText id="role-description" {...form.register('description')} disabled={readOnly} />
        </FormField>
      </div>
      {outOfOffer.length > 0 && !readOnly && (
        <Message severity="warn" text={t('roles.outOfOffer', { codes: outOfOffer.join(', ') })} />
      )}
      {permissions.isError ? (
        <ErrorMessage error={permissions.error} onRetry={() => void permissions.refetch()} />
      ) : (
        <Controller
          control={form.control}
          name="permissions"
          render={({ field }) => (
            <PermissionPicker
              permissions={permissions.data ?? []}
              value={field.value}
              onChange={field.onChange}
              readOnly={readOnly}
              // Anti-escalade (ergonomie) : seul le propriétaire accorde ce qu'il n'a pas.
              canGrant={(code) => capabilities.is_owner || can(code)}
            />
          )}
        />
      )}
      <div className="sm-dialog-actions">
        <Button
          type="button"
          label={t(readOnly ? 'actions.back' : 'actions.cancel')}
          text
          onClick={onClose}
        />
        {!readOnly && <Button type="submit" label={t('actions.save')} loading={save.isPending} />}
      </div>
    </form>
  );

  return (
    <Dialog header={header} visible onHide={onClose} className="sm-dialog sm-dialog-wide">
      {role && can('users.member.view') ? (
        <TabView>
          <TabPanel header={t('roles.permissions')}>{details}</TabPanel>
          <TabPanel header={t('roles.membersTab', { count: role.member_count })}>
            <RoleMembers role={role} />
          </TabPanel>
        </TabView>
      ) : (
        details
      )}
    </Dialog>
  );
}

function DuplicateDialog({ role, onClose }: { role: Role; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const duplicate = useDuplicateRole();
  const [name, setName] = useState(t('roles.copyOf', { name: role.name }));
  const [description, setDescription] = useState(role.description ?? '');
  const valid = name.trim().length > 0 && name.trim().length <= 100;

  const submit = () =>
    duplicate.mutate(
      { id: role.id, name: name.trim(), description: description.trim() || null },
      {
        onSuccess: () => {
          toast.success(t('roles.duplicated'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );

  return (
    <Dialog header={t('roles.duplicate')} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        <p className="sm-help">{t('roles.duplicateHelp', { name: role.name })}</p>
        <FormField id="duplicate-name" label={t('roles.name')} required>
          <InputText
            id="duplicate-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
        </FormField>
        <FormField id="duplicate-description" label={t('roles.description')}>
          <InputText
            id="duplicate-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            label={t('roles.duplicate')}
            disabled={!valid}
            loading={duplicate.isPending}
            onClick={submit}
          />
        </div>
      </div>
    </Dialog>
  );
}

export default function RolesPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const roles = useRoles();
  const setActive = useSetRoleActive();
  const [editing, setEditing] = useState<Role | null | undefined>(undefined);
  const [duplicating, setDuplicating] = useState<Role | null>(null);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const canManage = can('users.role.manage');
  const templates = useRoleTemplates(canManage);
  const fromTemplate = useCreateRoleFromTemplate();
  const missingTemplates = (templates.data ?? []).filter((tpl) => !tpl.instantiated);

  const term = normalizeSearch(search);
  const visible = (roles.data ?? []).filter(
    (r) =>
      (status === 'all' || r.is_active === (status === 'active')) &&
      (term === '' || normalizeSearch(`${r.name} ${r.description ?? ''}`).includes(term)),
  );

  const toggle = (role: Role, confirm = false) =>
    setActive.mutate(
      { id: role.id, active: !role.is_active, confirm },
      {
        onSuccess: () => toast.success(t('roles.statusChanged')),
        onError: (error) => {
          if (error instanceof ApiError && error.code === 'role_in_use') {
            const members = Array.isArray(error.extra.members)
              ? (error.extra.members as { full_name: string }[])
              : [];
            confirmAction(t, {
              header: t('roles.deactivateTitle', { name: role.name }),
              message: t('roles.confirmDeactivate', {
                count: Number(error.extra.count ?? members.length),
                names: [...new Set(members.map((m) => m.full_name))].join(', '),
              }),
              acceptLabel: t('actions.deactivate'),
              danger: true,
              onAccept: () => toggle(role, true),
            });
            return;
          }
          toast.error(translateError(t, error));
        },
      },
    );

  const actions = (r: Role) => (
    <RowActions
      actions={[
        {
          key: 'open',
          label: t(r.is_system || !canManage ? 'roles.view' : 'actions.edit'),
          icon: r.is_system || !canManage ? 'pi pi-eye' : 'pi pi-pencil',
          onClick: () => setEditing(r),
        },
        {
          key: 'duplicate',
          label: t('roles.duplicate'),
          icon: 'pi pi-copy',
          onClick: () => setDuplicating(r),
          hidden: !canManage,
        },
        {
          key: 'status',
          label: t(r.is_active ? 'actions.deactivate' : 'actions.activate'),
          icon: r.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
          danger: r.is_active,
          onClick: () => toggle(r),
          hidden: !canManage || r.protected,
        },
      ]}
    />
  );

  const table = (items: Role[], empty: string) => (
    <DataTable
      className="sm-table"
      value={items}
      loading={roles.isPending}
      dataKey="id"
      rowHover
      tableStyle={{ minWidth: '44rem' }}
      emptyMessage={<EmptyState icon="pi pi-shield" title={empty} />}
      rowClassName={(r: Role) => (r.is_active ? '' : 'sm-row-inactive')}
    >
      <Column
        header={t('roles.name')}
        body={(r: Role) => (
          <div className="sm-tags">
            <span>{r.name}</span>
            <RoleTags role={r} />
          </div>
        )}
      />
      <Column field="description" header={t('roles.description')} />
      <Column header={t('roles.permissions')} body={(r: Role) => r.permission_codes.length} />
      <Column header={t('roles.members')} body={(r: Role) => r.member_count} />
      <Column header={t('common.actions')} body={actions} />
    </DataTable>
  );

  return (
    <>
      <PageHeader
        title={t('roles.title')}
        description={t('roles.subtitle')}
        actions={
          canManage && (
            <Button icon="pi pi-plus" label={t('roles.new')} onClick={() => setEditing(null)} />
          )
        }
      />
      {missingTemplates.length > 0 && (
        <div className="sm-toolbar" aria-label={t('roles.templates')}>
          {missingTemplates.map((tpl) => (
            <Button
              key={tpl.code}
              icon="pi pi-plus"
              outlined
              label={t('roles.addTemplate', { name: tpl.name })}
              loading={fromTemplate.isPending}
              onClick={() =>
                fromTemplate.mutate(tpl.code, {
                  onSuccess: () => toast.success(t('roles.created')),
                  onError: (error) => toast.error(translateError(t, error)),
                })
              }
            />
          ))}
        </div>
      )}
      <div className="sm-toolbar">
        <SearchInput value={search} onChange={setSearch} />
        <StatusFilter value={status} onChange={setStatus} />
      </div>
      {roles.isError ? (
        <ErrorMessage error={roles.error} onRetry={() => void roles.refetch()} />
      ) : (
        <>
          <section className="sm-block" aria-labelledby="roles-system">
            <h2 id="roles-system" className="sm-section-title">
              {t('roles.systemRoles')}
            </h2>
            <p className="sm-help">{t('roles.systemRolesHelp')}</p>
            {table(
              visible.filter((r) => r.is_system),
              t('common.noData'),
            )}
          </section>
          <section className="sm-block" aria-labelledby="roles-custom">
            <h2 id="roles-custom" className="sm-section-title">
              {t('roles.customRoles')}
            </h2>
            <p className="sm-help">{t('roles.customRolesHelp')}</p>
            {table(
              visible.filter((r) => !r.is_system),
              t('roles.noCustomRole'),
            )}
          </section>
        </>
      )}
      {editing !== undefined && <RoleDialog role={editing} onClose={() => setEditing(undefined)} />}
      {duplicating && <DuplicateDialog role={duplicating} onClose={() => setDuplicating(null)} />}
    </>
  );
}
