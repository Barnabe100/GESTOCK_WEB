import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Column } from 'primereact/column';
import { confirmDialog, ConfirmDialog } from 'primereact/confirmdialog';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
import { Tag } from 'primereact/tag';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';

import {
  useCreateRoleFromTemplate,
  useDeleteRole,
  usePermissions,
  useRoles,
  useRoleTemplates,
  useSaveRole,
  type Permission,
  type Role,
} from './api';

const schema = z.object({
  name: z.string().trim().min(1).max(100),
  description: z.string().max(500),
  permissions: z.array(z.string()),
});
type FormValues = z.infer<typeof schema>;

function groupByModule(permissions: Permission[]): [string, Permission[]][] {
  const groups = new Map<string, Permission[]>();
  for (const permission of permissions) {
    groups.set(permission.module, [...(groups.get(permission.module) ?? []), permission]);
  }
  return [...groups.entries()];
}

function RoleDialog({ role, onClose }: { role: Role | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const permissions = usePermissions();
  const save = useSaveRole();
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
      { id: role?.id, input: { ...values, description: values.description || null } },
      {
        onSuccess: () => {
          toast.success(t(role ? 'roles.updated' : 'roles.created'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    ),
  );

  return (
    <Dialog
      header={t(role ? 'roles.edit' : 'roles.new')}
      visible
      onHide={onClose}
      className="sm-dialog sm-dialog-wide"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <FormField
          id="role-name"
          label={t('roles.name')}
          error={form.formState.errors.name && t('validation.required')}
        >
          <InputText id="role-name" {...form.register('name')} autoFocus />
        </FormField>
        <FormField id="role-description" label={t('roles.description')}>
          <InputText id="role-description" {...form.register('description')} />
        </FormField>
        <fieldset className="sm-fieldset">
          <legend>{t('roles.permissions')}</legend>
          <Controller
            control={form.control}
            name="permissions"
            render={({ field }) => (
              <>
                {groupByModule(permissions.data ?? []).map(([module, items]) => (
                  <div key={module} className="sm-permission-group">
                    <strong>{t(`modules.${module}`)}</strong>
                    {items.map((permission) => {
                      const id = `perm-${permission.code}`;
                      // Anti-escalade (ergonomie) : seul le propriétaire accorde ce qu'il n'a pas.
                      const grantable = capabilities.is_owner || can(permission.code);
                      return (
                        <div key={permission.code} className="sm-checkbox">
                          <Checkbox
                            inputId={id}
                            checked={field.value.includes(permission.code)}
                            disabled={!grantable}
                            onChange={(e) =>
                              field.onChange(
                                e.checked
                                  ? [...field.value, permission.code]
                                  : field.value.filter((c) => c !== permission.code),
                              )
                            }
                          />
                          <label htmlFor={id}>
                            {t(`permissions.${permission.code}`, permission.code)}
                          </label>
                        </div>
                      );
                    })}
                  </div>
                ))}
              </>
            )}
          />
        </fieldset>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}

export default function RolesPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const roles = useRoles();
  const remove = useDeleteRole();
  const [editing, setEditing] = useState<Role | null | undefined>(undefined);
  const canManage = can('users.role.manage');
  const templates = useRoleTemplates(canManage);
  const fromTemplate = useCreateRoleFromTemplate();
  const missingTemplates = (templates.data ?? []).filter((tpl) => !tpl.instantiated);

  const onDelete = (role: Role) =>
    confirmDialog({
      message: t('roles.confirmDelete', { name: role.name }),
      acceptLabel: t('actions.delete'),
      rejectLabel: t('actions.cancel'),
      acceptClassName: 'p-button-danger',
      accept: () =>
        remove.mutate(role.id, {
          onSuccess: () => toast.success(t('roles.deleted')),
          onError: (error) => toast.error(translateError(t, error)),
        }),
    });

  return (
    <>
      <ConfirmDialog />
      <PageHeader
        title={t('roles.title')}
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
      {roles.isError ? (
        <ErrorMessage error={roles.error} onRetry={() => void roles.refetch()} />
      ) : (
        <DataTable value={roles.data ?? []} loading={roles.isPending} dataKey="id">
          <Column
            header={t('roles.name')}
            body={(r: Role) => (
              <div className="sm-tags">
                <span>{r.name}</span>
                {r.is_system && <Tag severity="secondary" value={t('roles.system')} />}
              </div>
            )}
          />
          <Column field="description" header={t('roles.description')} />
          <Column header={t('roles.permissions')} body={(r: Role) => r.permission_codes.length} />
          {canManage && (
            <Column
              body={(r: Role) =>
                r.is_system ? null : (
                  <div className="sm-row-actions">
                    <Button
                      icon="pi pi-pencil"
                      text
                      aria-label={t('actions.edit')}
                      onClick={() => setEditing(r)}
                    />
                    <Button
                      icon="pi pi-trash"
                      text
                      severity="danger"
                      aria-label={t('actions.delete')}
                      onClick={() => onDelete(r)}
                    />
                  </div>
                )
              }
            />
          )}
        </DataTable>
      )}
      {editing !== undefined && <RoleDialog role={editing} onClose={() => setEditing(undefined)} />}
    </>
  );
}
