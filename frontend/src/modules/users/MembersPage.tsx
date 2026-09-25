import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { MultiSelect } from 'primereact/multiselect';
import { Password } from 'primereact/password';
import { useState } from 'react';
import { Controller, useFieldArray, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { useSites } from '@/modules/organization/api';
import { translateError } from '@/shared/lib/errors';
import { formatDate } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type StatusFilterValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { useCreateRequest } from '@/shared/lib/useCreateRequest';
import { confirmAction } from '@/shared/ui/confirm';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusFilter } from '@/shared/ui/StatusFilter';
import { RowActions } from '@/shared/ui/RowActions';

import {
  useDelegableRoles,
  useMembers,
  useRoles,
  useSaveMember,
  useSetMemberActive,
  type Member,
} from './api';

const schema = z.object({
  email: z.string().trim().email(),
  full_name: z.string().trim().min(1).max(150),
  password: z.string().max(256),
  role_ids: z.array(z.string()),
  // Rôles limités à un site (ex. « Responsable boutique » sur la seule boutique).
  site_roles: z.array(z.object({ role_id: z.string().min(1), site_id: z.string().min(1) })),
  site_ids: z.array(z.string()),
  all_sites: z.boolean(),
  status: z.enum(['active', 'suspended']),
});
type FormValues = z.infer<typeof schema>;

/**
 * Rôle limité à un site : options attribuables pour CE site, selon le serveur (un rôle limité à
 * un site se délègue avec les droits détenus sur ce site).
 */
function SiteRoleDropdown({
  siteId,
  value,
  onChange,
  options,
  placeholder,
  ariaLabel,
  invalid,
}: {
  siteId: string | null;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string; disabled: boolean }[];
  placeholder: string;
  ariaLabel: string;
  invalid: boolean;
}) {
  const delegable = useDelegableRoles(siteId, siteId !== null);
  const ids = new Set((delegable.data ?? []).map((r) => r.id));
  return (
    <Dropdown
      value={value}
      onChange={(e) => onChange(e.value as string)}
      options={options.map((o) => ({ ...o, disabled: siteId === null || !ids.has(o.value) }))}
      optionDisabled="disabled"
      placeholder={placeholder}
      aria-label={ariaLabel}
      invalid={invalid}
    />
  );
}

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
      role_ids: member?.roles.filter((r) => !r.site_id).map((r) => r.role_id) ?? [],
      site_roles:
        member?.roles
          .filter((r) => r.site_id)
          .map((r) => ({ role_id: r.role_id, site_id: r.site_id ?? '' })) ?? [],
      site_ids: member?.site_ids ?? [],
      all_sites: member?.all_sites ?? false,
      status: member?.status ?? 'active',
    },
  });
  const errors = form.formState.errors;
  const allSites = useWatch({ control: form.control, name: 'all_sites' });
  const siteRoles = useFieldArray({ control: form.control, name: 'site_roles' });
  const siteRoleSites = (useWatch({ control: form.control, name: 'site_roles' }) ?? []).map(
    (r) => r?.site_id ?? '',
  );
  const { capabilities } = useCapabilities();

  // Rôles proposés : actifs ; un rôle désactivé déjà attribué reste affiché (et conservé).
  // Attribuables : ceux que le serveur déclare délégables (tout le tenant ; par site pour les
  // rôles limités à un site) — le serveur refuse de toute façon le reste.
  const assigned = new Set(member?.roles.map((r) => r.role_id) ?? []);
  const delegable = useDelegableRoles();
  const delegableIds = new Set((delegable.data ?? []).map((r) => r.id));
  const roleOptions = (roles.data ?? [])
    .filter((r) => r.is_active || assigned.has(r.id))
    .map((r) => ({
      value: r.id,
      label: !r.is_active
        ? `${r.name} (${t('common.inactive').toLowerCase()})`
        : delegableIds.has(r.id)
          ? r.name
          : `${r.name} (${t('roles.notDelegable').toLowerCase()})`,
      disabled: !r.is_active || !delegableIds.has(r.id),
    }));
  // Sites proposés : ceux de l'utilisateur courant (le backend refuse tout autre site).
  const siteNames = new Map((sites.data ?? []).map((s) => [s.id, s.name]));
  const siteOptions = capabilities.sites.map((s) => ({ value: s.id, label: s.name }));
  for (const id of member?.site_ids ?? []) {
    if (!siteOptions.some((o) => o.value === id)) {
      siteOptions.push({ value: id, label: siteNames.get(id) ?? '…' });
    }
  }

  const onSubmit = form.handleSubmit((values) => {
    const tenantWide = values.role_ids.map((role_id) => ({ role_id, site_id: null }));
    const siteScoped = values.site_roles.map(({ role_id, site_id }) => ({ role_id, site_id }));
    // Un rôle limité à un site exige l'accès à ce site : ajouté automatiquement.
    const site_ids = values.all_sites
      ? []
      : [...new Set([...values.site_ids, ...siteScoped.map((r) => r.site_id)])];
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
            roles: [...tenantWide, ...siteScoped],
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
      header={t(editing ? 'members.editAccess' : 'members.new')}
      visible
      onHide={onClose}
      className="sm-dialog sm-dialog-wide"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        {editing ? (
          // Identité globale : affichée, jamais modifiable depuis une entreprise (ADR-0029).
          <section className="sm-identity-readonly" aria-labelledby="member-identity">
            <h3 id="member-identity">{t('members.identity')}</h3>
            <dl className="sm-details">
              <div>
                <dt>{t('members.name')}</dt>
                <dd data-testid="member-identity-name">{member.full_name}</dd>
              </div>
              <div>
                <dt>{t('members.email')}</dt>
                <dd data-testid="member-identity-email">{member.email}</dd>
              </div>
            </dl>
            <p className="sm-help">{t('members.identityHelp')}</p>
          </section>
        ) : (
          <>
            <FormField
              id="member-email"
              label={t('members.email')}
              required
              help={t('members.emailHelp')}
              error={errors.email && t('validation.email')}
            >
              <InputText id="member-email" type="email" {...form.register('email')} autoFocus />
            </FormField>
            <FormField
              id="member-name"
              label={t('members.name')}
              required
              error={errors.full_name && t('validation.required')}
            >
              <InputText id="member-name" {...form.register('full_name')} />
            </FormField>
          </>
        )}
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
                options={roleOptions}
                optionDisabled="disabled"
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
                  options={siteOptions}
                  display="chip"
                />
              )}
            />
          </FormField>
        )}
        <fieldset className="sm-fieldset">
          <legend>{t('members.siteRoles')}</legend>
          <small className="sm-help">{t('members.siteRolesHelp')}</small>
          {siteRoles.fields.map((field, index) => (
            <div key={field.id} className="sm-site-role">
              <Controller
                control={form.control}
                name={`site_roles.${index}.role_id`}
                render={({ field: f }) => (
                  <SiteRoleDropdown
                    siteId={siteRoleSites[index] || null}
                    value={f.value}
                    onChange={f.onChange}
                    options={roleOptions}
                    placeholder={t('members.chooseRole')}
                    ariaLabel={t('members.roles')}
                    invalid={Boolean(errors.site_roles?.[index]?.role_id)}
                  />
                )}
              />
              <Controller
                control={form.control}
                name={`site_roles.${index}.site_id`}
                render={({ field: f }) => (
                  <Dropdown
                    value={f.value}
                    onChange={(e) => f.onChange(e.value)}
                    options={siteOptions}
                    placeholder={t('members.chooseSite')}
                    aria-label={t('layout.site')}
                    invalid={Boolean(errors.site_roles?.[index]?.site_id)}
                  />
                )}
              />
              <Button
                type="button"
                icon="pi pi-trash"
                text
                severity="danger"
                aria-label={t('members.removeSiteRole')}
                onClick={() => siteRoles.remove(index)}
              />
            </div>
          ))}
          <div>
            <Button
              type="button"
              icon="pi pi-plus"
              outlined
              size="small"
              label={t('members.addSiteRole')}
              onClick={() => siteRoles.append({ role_id: '', site_id: '' })}
            />
          </div>
        </fieldset>
        {editing && (
          <FormField id="member-status" label={t('members.status')} help={t('members.statusHelp')}>
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

/** Activation / désactivation de l'appartenance (désactivation confirmée), avec retour. */
function useMemberStatus() {
  const { t } = useTranslation();
  const toast = useToast();
  const setActive = useSetMemberActive();
  const run = (member: Member, active: boolean) =>
    setActive.mutate(
      { id: member.id, active },
      {
        onSuccess: () => toast.success(t(active ? 'members.activated' : 'members.deactivated')),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  return (member: Member) => {
    if (member.status !== 'active') return run(member, true);
    confirmAction(t, {
      header: t('members.deactivateTitle'),
      message: t('members.deactivateConfirm', { name: member.full_name }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: () => run(member, false),
    });
  };
}

export default function MembersPage() {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const roles = useRoles();
  const sites = useSites();
  const canManage = can('users.member.manage');
  const [createRequested, clearCreate] = useCreateRequest(canManage);
  const [editing, setEditing] = useState<Member | null | undefined>(
    createRequested ? null : undefined,
  );
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'full_name',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const [roleId, setRoleId] = useState<string | null>(null);
  const [siteId, setSiteId] = useState<string | null>(null);
  const debounced = useDebouncedValue(search);
  const members = useMembers(
    toQueryString(table, { search: debounced, status, role_id: roleId, site_id: siteId }),
  );
  const toggle = useMemberStatus();

  const roleNames = new Map(
    (roles.data ?? []).map((r) => [
      r.id,
      r.is_active ? r.name : `${r.name} (${t('common.inactive').toLowerCase()})`,
    ]),
  );
  const siteNames = new Map((sites.data ?? []).map((s) => [s.id, s.name]));
  const resetPage = () => setTable((state) => ({ ...state, first: 0 }));
  const filtered = search !== '' || status !== 'all' || roleId !== null || siteId !== null;
  const resetFilters = () => {
    setSearch('');
    setStatus('all');
    setRoleId(null);
    setSiteId(null);
    resetPage();
  };
  // Seul le propriétaire (ou l'utilisateur courant) : inviter à ajouter l'équipe.
  const alone = !filtered && members.data !== undefined && members.data.total <= 1;
  const addButton = (outlined = false) =>
    canManage && (
      <Button
        icon="pi pi-user-plus"
        label={t('members.new')}
        outlined={outlined}
        onClick={() => setEditing(null)}
      />
    );

  return (
    <>
      <PageHeader
        title={t('members.title')}
        description={t('members.subtitle')}
        actions={addButton()}
      />
      <FilterBar onReset={resetFilters} active={filtered}>
        <SearchInput
          value={search}
          placeholder={t('members.search')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={roleId}
          onChange={(e) => {
            setRoleId((e.value as string | undefined) ?? null);
            resetPage();
          }}
          options={(roles.data ?? []).map((r) => ({ value: r.id, label: roleNames.get(r.id) }))}
          placeholder={t('members.allRoles')}
          showClear
          filter
          aria-label={t('members.roles')}
        />
        <Dropdown
          value={siteId}
          onChange={(e) => {
            setSiteId((e.value as string | undefined) ?? null);
            resetPage();
          }}
          options={(sites.data ?? []).map((s) => ({ value: s.id, label: s.name }))}
          placeholder={t('members.allSitesFilter')}
          showClear
          aria-label={t('members.sites')}
        />
        <StatusFilter
          value={status}
          onChange={(v) => {
            setStatus(v);
            resetPage();
          }}
        />
      </FilterBar>
      {alone && (
        <Message
          severity="info"
          className="sm-block"
          data-testid="members-alone"
          content={
            <div className="sm-message-with-action">
              <span>{t('members.alone')}</span>
              {addButton(true)}
            </div>
          }
        />
      )}
      <ServerTable
        query={members}
        table={table}
        onTableChange={setTable}
        empty={
          <ListEmpty filtered={filtered} title={t('members.empty')} action={addButton(true)} />
        }
      >
        <Column
          field="full_name"
          header={t('members.name')}
          sortable
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
              <StatusBadge tone="info" icon="pi pi-star" label={t('auth.owner')} />
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
          field="status"
          header={t('members.status')}
          sortable
          body={(m: Member) => (
            <div className="sm-tags">
              <StatusBadge
                tone={m.status === 'active' ? 'success' : 'neutral'}
                label={t(`members.statuses.${m.status}`)}
              />
              {m.must_change_password && (
                <StatusBadge tone="warning" label={t('members.mustChange')} />
              )}
            </div>
          )}
        />
        <Column
          field="created_at"
          header={t('members.addedAt')}
          sortable
          bodyClassName="sm-nowrap"
          body={(m: Member) => formatDate(m.created_at, locale, timezone)}
        />
        {canManage && (
          <Column
            header={t('common.actions')}
            body={(m: Member) => {
              // Ni le propriétaire ni son propre accès (refusés aussi par le serveur).
              const locked = m.is_owner || m.user_id === capabilities.user.id;
              return (
                <RowActions
                  actions={[
                    {
                      key: 'edit',
                      label: t('members.editAccess'),
                      icon: 'pi pi-pencil',
                      onClick: () => setEditing(m),
                      hidden: locked,
                    },
                    {
                      key: 'status',
                      label: t(m.status === 'active' ? 'actions.deactivate' : 'actions.activate'),
                      icon: m.status === 'active' ? 'pi pi-ban' : 'pi pi-check-circle',
                      danger: m.status === 'active',
                      onClick: () => toggle(m),
                      hidden: locked,
                    },
                  ]}
                />
              );
            }}
          />
        )}
      </ServerTable>
      {editing !== undefined && (
        <MemberDialog
          member={editing}
          onClose={() => {
            setEditing(undefined);
            clearCreate();
          }}
        />
      )}
    </>
  );
}
