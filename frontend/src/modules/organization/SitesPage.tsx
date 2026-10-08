import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputSwitch } from 'primereact/inputswitch';
import { InputText } from 'primereact/inputtext';
import { useState } from 'react';
import { Link } from 'react-router';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import type { SiteKind } from '@/core/api/types';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { useCreateRequest } from '@/shared/lib/useCreateRequest';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';
import { ActiveBadge, SubscriptionStatusBadge } from '@/shared/ui/StatusBadge';
import { EmptyState } from '@/shared/ui/EmptyState';
import { RowActions } from '@/shared/ui/RowActions';

import { useSubscriptions } from '@/modules/subscription/api';
import { usePublicPlans } from '@/pages/signup/api';

import { profileLabel, sectorLabel } from '@/core/capabilities/profile';

import {
  useBusinessProfiles,
  useSaveSite,
  useSites,
  useSitesModules,
  type Site,
  type SiteModule,
} from './api';
import { moduleState } from './moduleState';
import { SiteProfileDialog } from './SiteProfileDialog';

const KINDS: SiteKind[] = ['store', 'warehouse', 'restaurant', 'other'];

type SiteRow = Site & { activeModules: SiteModule[] };

const schema = z.object({
  plan_code: z.string(),
  billing_period: z.enum(['monthly', 'annual']),
  requested_activations: z.number().int().min(1).max(1000),
  name: z.string().trim().min(1).max(150),
  code: z
    .string()
    .trim()
    .regex(/^[A-Za-z0-9_-]{1,30}$/),
  kind: z.enum(['store', 'warehouse', 'restaurant', 'other']),
  address: z.string().max(255),
  phone: z.string().max(50),
  is_active: z.boolean(),
  business_profile_code: z.string(),
});
type FormValues = z.infer<typeof schema>;

/**
 * Création ou modification d'un site. Création : 1 site = 1 abonnement (ADR-0033) — offre
 * publiée, période et nombre de postes demandés, sauf pour le premier site d'une inscription
 * (abonnement déjà choisi). Le nouvel abonnement attend le paiement et la licence ; le serveur
 * revérifie tout.
 */
function SiteDialog({
  site,
  choosePlan,
  offerKnown,
  initialProfile,
  onClose,
}: {
  site: Site | null;
  choosePlan: boolean;
  /** Abonnement d'inscription connu (liste des abonnements chargée) : tant qu'il ne l'est pas,
   * ``choosePlan`` peut encore changer, l'enregistrement attend (aucun clic perdu). */
  offerKnown: boolean;
  /** Profil proposé à la création (ex. « Créer un nouveau site avec ce profil »). */
  initialProfile?: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { can } = useCapabilities();
  const toast = useToast();
  const save = useSaveSite();
  const plans = usePublicPlans();
  const offers = (plans.data?.plans ?? []).filter((p) => p.self_service);
  const creating = site === null;
  const withPlan = creating && choosePlan;
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      plan_code: '',
      billing_period: 'monthly',
      requested_activations: 1,
      name: site?.name ?? '',
      code: site?.code ?? '',
      kind: site?.kind ?? 'store',
      address: site?.address ?? '',
      phone: site?.phone ?? '',
      is_active: site?.is_active ?? true,
      business_profile_code: initialProfile ?? '',
    },
  });
  const errors = form.formState.errors;
  const [planError, setPlanError] = useState(false);
  const planCode = useWatch({ control: form.control, name: 'plan_code' });
  const chosenPlan = offers.find((p) => p.code === planCode);
  // Profil du nouveau site (palier D) : absent → profil d'origine de l'entreprise ; un autre
  // profil exige ``organization.profile.manage`` (revérifié par le serveur).
  const chooseProfile = creating && can('organization.profile.manage');
  const catalog = useBusinessProfiles(chooseProfile);
  const profileGroups = (catalog.data?.sectors ?? [])
    .map((sector) => ({
      label: sectorLabel(t, sector),
      items: (catalog.data?.profiles ?? [])
        .filter((p) => p.sector === sector.code)
        .map((p) => ({ value: p.code, label: profileLabel(t, p) })),
    }))
    .filter((g) => g.items.length > 0);

  const onSubmit = form.handleSubmit(
    ({
      is_active,
      plan_code,
      billing_period,
      requested_activations,
      business_profile_code,
      ...values
    }) => {
      if (withPlan && !plan_code) {
        setPlanError(true);
        return;
      }
      const input = {
        ...values,
        address: values.address || null,
        phone: values.phone || null,
        ...(site ? { is_active } : {}),
        ...(creating ? { requested_activations } : {}),
        ...(withPlan ? { plan_code, billing_period } : {}),
        ...(chooseProfile && business_profile_code ? { business_profile_code } : {}),
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
    },
  );

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
          required
          error={errors.name && t('validation.required')}
        >
          <InputText id="site-name" {...form.register('name')} autoFocus />
        </FormField>
        <FormField
          id="site-code"
          label={t('sites.code')}
          required
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
        {chooseProfile && (
          <FormField id="site-profile" label={t('sites.profile')} help={t('sites.profileHelp')}>
            <Controller
              control={form.control}
              name="business_profile_code"
              render={({ field }) => (
                <Dropdown
                  inputId="site-profile"
                  value={field.value || null}
                  placeholder={t('sites.originProfile')}
                  showClear
                  filter
                  onChange={(e) => field.onChange((e.value as string | undefined) ?? '')}
                  options={profileGroups}
                  optionGroupLabel="label"
                  optionGroupChildren="items"
                />
              )}
            />
          </FormField>
        )}
        {creating && (
          <fieldset className="sm-fieldset" data-testid="site-subscription">
            <legend>{t('sites.subscription')}</legend>
            <p className="sm-help">
              {t(withPlan ? 'sites.subscriptionHelp' : 'sites.subscriptionPreselected')}
            </p>
            {withPlan && (
              <>
                <FormField
                  id="site-plan"
                  label={t('sites.plan')}
                  required
                  error={planError ? t('validation.required') : undefined}
                >
                  <Controller
                    control={form.control}
                    name="plan_code"
                    render={({ field }) => (
                      <Dropdown
                        inputId="site-plan"
                        value={field.value}
                        placeholder={t('sites.choosePlan')}
                        emptyMessage={t('sites.noPlan')}
                        onChange={(e) => {
                          field.onChange(e.value);
                          setPlanError(false);
                          const periods = offers.find((p) => p.code === e.value)?.periods ?? [];
                          if (periods[0]) {
                            form.setValue('billing_period', periods[0].billing_period);
                          }
                        }}
                        options={offers.map((p) => ({ value: p.code, label: p.name }))}
                      />
                    )}
                  />
                </FormField>
                <FormField id="site-period" label={t('sites.billingPeriod')} required>
                  <Controller
                    control={form.control}
                    name="billing_period"
                    render={({ field }) => (
                      <Dropdown
                        inputId="site-period"
                        value={field.value}
                        onChange={(e) => field.onChange(e.value)}
                        options={(chosenPlan?.periods ?? []).map((p) => ({
                          value: p.billing_period,
                          label: t(`billingPeriod.${p.billing_period}`),
                        }))}
                      />
                    )}
                  />
                </FormField>
              </>
            )}
            <FormField
              id="site-activations"
              label={t('sites.requestedActivations')}
              help={t('sites.requestedActivationsHelp')}
              required
              error={errors.requested_activations && t('validation.invalid')}
            >
              <InputText
                id="site-activations"
                type="number"
                min={1}
                {...form.register('requested_activations', { valueAsNumber: true })}
              />
            </FormField>
          </fieldset>
        )}
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
          <Button
            type="submit"
            label={t('actions.save')}
            loading={save.isPending}
            disabled={!offerKnown}
          />
        </div>
      </form>
    </Dialog>
  );
}

export default function SitesPage() {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const sites = useSites();
  // Abonnement pris à l'inscription, pas encore rattaché : le premier site le reçoit.
  const subscriptions = useSubscriptions(can('subscription.subscription.view'));
  const preselected = (subscriptions.data ?? []).some((s) => s.site === null);
  const siteStatus = new Map(capabilities.sites.map((s) => [s.id, s.subscription_status]));
  const canManage = can('organization.site.manage');
  const canProfile = can('organization.profile.manage');
  // Libellé du profil lu depuis la ligne (`business_profile_code` de `/sites`, qui fait foi) :
  // les cellules du tableau ne se redessinent qu'au changement de leur ligne.
  const profileNames = new Map(
    capabilities.sites.flatMap((s) => (s.profile ? [[s.profile.code, s.profile.name]] : [])),
  );
  const [profileSite, setProfileSite] = useState<Site | null>(null);
  // Modules de chaque site (palier E) : SITE → PROFIL → MODULES → STATUT d'un coup d'œil.
  const canSeeModules = can('organization.module.view');
  const siteList = sites.data ?? [];
  const sitesModules = useSitesModules(
    siteList.map((s) => s.id),
    canSeeModules,
  );
  // Les modules sont portés par la ligne : les cellules du tableau ne se redessinent qu'au
  // changement de leur ligne (voir aussi la colonne Profil).
  const rows: SiteRow[] = siteList.map((s, index) => ({
    ...s,
    activeModules: (sitesModules[index]?.data ?? []).filter(
      (m) => !m.core && moduleState(m) === 'active',
    ),
  }));
  const [newSiteProfile, setNewSiteProfile] = useState<string | undefined>(undefined);
  // Action « Créer mon premier site » de l'onboarding : formulaire ouvert d'emblée.
  const [createRequested, clearCreate] = useCreateRequest(canManage);
  const [editing, setEditing] = useState<Site | null | undefined>(
    createRequested ? null : undefined,
  );

  return (
    <>
      <PageHeader
        title={t('sites.title')}
        description={t('sites.subtitle')}
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
          className="sm-table"
          value={rows}
          loading={sites.isPending}
          dataKey="id"
          rowHover
          tableStyle={{ minWidth: '36rem' }}
          emptyMessage={<EmptyState icon="pi pi-map-marker" title={t('sites.empty')} />}
        >
          <Column field="name" header={t('sites.name')} />
          <Column field="code" header={t('sites.code')} />
          <Column header={t('sites.kind')} body={(s: Site) => t(`sites.kinds.${s.kind}`)} />
          <Column
            header={t('sites.profile')}
            body={(s: Site) =>
              profileLabel(t, {
                code: s.business_profile_code,
                name: profileNames.get(s.business_profile_code) ?? s.business_profile_code,
              })
            }
          />
          {canSeeModules && (
            <Column
              header={t('sites.modules')}
              body={(s: SiteRow) => {
                const active = s.activeModules;
                return (
                  <span className="sm-stack-xs" data-testid={`site-modules-${s.code}`}>
                    <span title={active.map((m) => t(`modules.${m.code}`)).join(', ')}>
                      {t('sites.activeModules', { count: active.length })}
                    </span>
                    <Link to={`/organization/modules?site=${s.id}`}>{t('sites.seeModules')}</Link>
                  </span>
                );
              }}
            />
          )}
          <Column
            header={t('sites.status')}
            body={(s: Site) => <ActiveBadge active={s.is_active} />}
          />
          <Column
            header={t('sites.subscription')}
            body={(s: Site) => {
              const status = siteStatus.get(s.id);
              return status ? <SubscriptionStatusBadge status={status} /> : '—';
            }}
          />
          {(canManage || canProfile) && (
            <Column
              header={t('common.actions')}
              body={(s: Site) => (
                <RowActions
                  actions={[
                    ...(canManage
                      ? [
                          {
                            key: 'edit',
                            label: t('actions.edit'),
                            icon: 'pi pi-pencil',
                            onClick: () => setEditing(s),
                          },
                        ]
                      : []),
                    ...(canProfile && s.is_active
                      ? [
                          {
                            key: 'profile',
                            label: t('siteProfile.action'),
                            icon: 'pi pi-sync',
                            onClick: () => setProfileSite(s),
                          },
                        ]
                      : []),
                  ]}
                />
              )}
            />
          )}
        </DataTable>
      )}
      {editing !== undefined && (
        <SiteDialog
          site={editing}
          choosePlan={!preselected}
          offerKnown={!subscriptions.isLoading}
          initialProfile={newSiteProfile}
          onClose={() => {
            setEditing(undefined);
            setNewSiteProfile(undefined);
            clearCreate();
          }}
        />
      )}
      {profileSite && (
        <SiteProfileDialog
          site={profileSite}
          onClose={() => setProfileSite(null)}
          onCreateSite={
            canManage
              ? (code) => {
                  // Opération séparée : un nouveau site avec ce profil, rien n'est copié.
                  setProfileSite(null);
                  setNewSiteProfile(code);
                  setEditing(null);
                }
              : undefined
          }
        />
      )}
    </>
  );
}
