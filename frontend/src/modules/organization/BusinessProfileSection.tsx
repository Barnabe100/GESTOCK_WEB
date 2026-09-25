import { Button } from 'primereact/button';
import { Dropdown } from 'primereact/dropdown';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { profileLabel, sectorLabel } from '@/core/capabilities/profile';
import { translateError } from '@/shared/lib/errors';
import { confirmAction } from '@/shared/ui/confirm';
import { FormField } from '@/shared/ui/FormField';
import { FormSection } from '@/shared/ui/FormSection';
import { useToast } from '@/shared/ui/toast';

import { useBusinessProfiles, useChangeBusinessProfile } from './api';

interface ProfileOption {
  label: string;
  value: string;
}

/**
 * Profil d'activité de l'entreprise : affiché à tous ; modifiable avec
 * `organization.profile.manage` (le serveur refuse sinon, et refuse un profil incompatible
 * avec les modules activés). Aucune donnée n'est supprimée.
 */
export function BusinessProfileSection() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const { profile } = capabilities;
  const canManage = can('organization.profile.manage');
  const catalog = useBusinessProfiles(canManage && can('organization.profile.view'));
  const change = useChangeBusinessProfile();
  const [selected, setSelected] = useState<string | null>(null);

  const groups = useMemo(() => {
    const data = catalog.data;
    if (!data) return [];
    return data.sectors.map((sector) => ({
      label: sectorLabel(t, sector),
      items: data.profiles
        .filter((p) => p.sector === sector.code)
        .map<ProfileOption>((p) => ({ label: profileLabel(t, p), value: p.code })),
    }));
  }, [catalog.data, t]);

  const submit = () => {
    const target = catalog.data?.profiles.find((p) => p.code === selected);
    if (!target) return;
    confirmAction(t, {
      header: t('company.confirmProfileTitle'),
      message: t('company.confirmProfile', { profile: profileLabel(t, target) }),
      acceptLabel: t('company.changeProfile'),
      onAccept: () =>
        change.mutate(target.code, {
          onSuccess: () => {
            setSelected(null);
            toast.success(t('company.profileChanged'));
          },
          onError: (error) => {
            if (error instanceof ApiError && error.code === 'profile_change_incompatible') {
              const modules = (error.extra.modules as string[] | undefined) ?? [];
              toast.error(
                t('company.profileIncompatible', {
                  modules: modules.map((code) => t(`modules.${code}`)).join(', '),
                }),
              );
            } else {
              toast.error(translateError(t, error));
            }
          },
        }),
    });
  };

  return (
    <FormSection title={t('company.profileSection')} description={t('company.profileHelp')}>
      <dl className="sm-details">
        <div>
          <dt>{t('company.sector')}</dt>
          <dd>{profile.sector ? sectorLabel(t, profile.sector) : '—'}</dd>
        </div>
        <div>
          <dt>{t('company.profile')}</dt>
          <dd data-testid="company-profile">{profileLabel(t, profile)}</dd>
        </div>
      </dl>
      {canManage && (
        <div className="sm-form-grid">
          <FormField id="new-business-profile" label={t('company.newProfile')}>
            <Dropdown
              inputId="new-business-profile"
              value={selected}
              options={groups}
              optionGroupLabel="label"
              optionGroupChildren="items"
              optionLabel="label"
              optionValue="value"
              filter
              loading={catalog.isPending}
              placeholder={profileLabel(t, profile)}
              onChange={(e) => setSelected(e.value as string)}
            />
          </FormField>
          <div className="sm-form-actions">
            <Button
              type="button"
              icon="pi pi-sync"
              label={t('company.changeProfile')}
              disabled={!selected || selected === profile.code}
              loading={change.isPending}
              onClick={submit}
            />
          </div>
        </div>
      )}
    </FormSection>
  );
}
