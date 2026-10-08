import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { profileLabel, sectorLabel } from '@/core/capabilities/profile';
import { translateError } from '@/shared/lib/errors';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  useBusinessProfiles,
  useChangeSiteProfile,
  useSiteProfilePreview,
  type FootprintItem,
  type ProfileChangeLevel,
  type ProfileModuleChange,
  type Site,
  type SiteProfilePreview,
} from './api';

const LEVEL_TONES: Record<ProfileChangeLevel, Tone> = {
  SIMPLE: 'success',
  STRONG: 'warning',
  BLOCKED: 'danger',
};

function Items({ items, title }: { items: FootprintItem[]; title: string }) {
  const { t } = useTranslation();
  if (items.length === 0) return null;
  return (
    <section>
      <h4>{title}</h4>
      <ul>
        {items.map((item) => (
          <li key={`${item.module}.${item.kind}`}>
            {t(`siteProfile.kinds.${item.kind}`, { defaultValue: item.kind })} :{' '}
            {item.capped ? t('siteProfile.capped', { count: item.count }) : item.count}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ModuleList({ modules, title }: { modules: ProfileModuleChange[]; title: string }) {
  const { t } = useTranslation();
  if (modules.length === 0) return null;
  return (
    <section>
      <h4>{title}</h4>
      <ul>
        {modules.map((m) => (
          <li key={m.code} data-testid={`module-${m.code}`}>
            {t(`modules.${m.code}`)}{' '}
            {m.status === 'planned' && (
              <StatusBadge tone="neutral" label={t('moduleAdmin.planned')} />
            )}
            {!m.after.in_plan && m.after.in_profile && (
              <StatusBadge tone="warning" label={t('moduleAdmin.notInPlan')} />
            )}
            {m.action === 'enable' && (
              <StatusBadge tone="success" label={t('siteProfile.activated')} />
            )}
            {m.action === 'disable' && (
              <StatusBadge tone="neutral" label={t('siteProfile.deactivated')} />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function PreviewDetails({ preview }: { preview: SiteProfilePreview }) {
  const { t } = useTranslation();
  const byChange = (change: ProfileModuleChange['change']) =>
    preview.modules.filter((m) => m.change === change);
  return (
    <div className="sm-stack" data-testid="profile-preview">
      <p>
        <StatusBadge
          tone={LEVEL_TONES[preview.level]}
          label={t(`siteProfile.levels.${preview.level}`)}
        />{' '}
        {t(`siteProfile.levelHelp.${preview.level}`)}
      </p>
      {preview.plan.compatibility === 'PARTIAL' && (
        <Message
          severity="warn"
          text={t('siteProfile.planPartial', {
            modules: preview.plan.modules_not_in_plan.map((c) => t(`modules.${c}`)).join(', '),
          })}
        />
      )}
      <ModuleList modules={byChange('added')} title={t('siteProfile.added')} />
      <ModuleList modules={byChange('removed')} title={t('siteProfile.removed')} />
      <ModuleList modules={byChange('kept')} title={t('siteProfile.kept')} />
      <Items items={preview.blockers} title={t('siteProfile.blockers')} />
      <Items items={preview.open_operations} title={t('siteProfile.openOperations')} />
      <Items items={preview.history} title={t('siteProfile.history')} />
      <p className="sm-help">{t('siteProfile.dataKept')}</p>
      {preview.assortment_unchanged && (
        <p className="sm-help">{t('siteProfile.assortmentUnchanged')}</p>
      )}
    </div>
  );
}

/**
 * Changement du profil d'UN site (palier D) : choix du profil, aperçu calculé par le serveur
 * (niveau, modules, données conservées, blocages), confirmation selon le niveau. Le serveur
 * recalcule tout sous verrou : l'interface ne décide jamais du niveau ni du résultat.
 */
export function SiteProfileDialog({
  site,
  onClose,
  onCreateSite,
}: {
  site: Site;
  onClose: () => void;
  /** Proposée si le changement est BLOCKED : création séparée d'un site avec ce profil. */
  onCreateSite?: (profileCode: string) => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const catalog = useBusinessProfiles(true);
  const [target, setTarget] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState('');
  const preview = useSiteProfilePreview(site.id, target);
  const change = useChangeSiteProfile();
  const data = preview.data;

  const sectors = catalog.data?.sectors ?? [];
  const groups = sectors
    .map((sector) => ({
      label: sectorLabel(t, sector),
      items: (catalog.data?.profiles ?? [])
        .filter((p) => p.sector === sector.code && p.code !== site.business_profile_code)
        .map((p) => ({ value: p.code, label: profileLabel(t, p) })),
    }))
    .filter((g) => g.items.length > 0);

  const confirmed =
    data !== undefined &&
    data.level !== 'BLOCKED' &&
    (data.level === 'SIMPLE' || confirmation === data.confirmation_text);

  const submit = () => {
    if (!data || !target || !confirmed) return;
    change.mutate(
      {
        siteId: site.id,
        profile_code: target,
        preview_fingerprint: data.fingerprint,
        ...(data.level === 'STRONG' ? { confirmation } : {}),
      },
      {
        onSuccess: () => {
          toast.success(t('siteProfile.changed'));
          onClose();
        },
        onError: (error) => {
          toast.error(translateError(t, error));
          // Aperçu périmé ou situation changée : nouvel aperçu, nouvelle confirmation.
          if (error instanceof ApiError && error.status === 409) {
            setConfirmation('');
            void preview.refetch();
          }
        },
      },
    );
  };

  return (
    <Dialog
      header={t('siteProfile.title', { site: site.name })}
      visible
      onHide={onClose}
      className="sm-dialog sm-dialog-wide"
    >
      <div className="sm-form">
        <p className="sm-help">{t('siteProfile.intro')}</p>
        <FormField id="site-profile-target" label={t('siteProfile.target')} required>
          <Dropdown
            inputId="site-profile-target"
            value={target}
            options={groups}
            optionGroupLabel="label"
            optionGroupChildren="items"
            placeholder={t('siteProfile.chooseProfile')}
            onChange={(e) => {
              setTarget((e.value as string | undefined) ?? null);
              setConfirmation('');
            }}
            filter
          />
        </FormField>
        {preview.isError && (
          <ErrorMessage error={preview.error} onRetry={() => void preview.refetch()} />
        )}
        {preview.isFetching && !data && <p className="sm-help">{t('common.loading')}</p>}
        {data && <PreviewDetails preview={data} />}
        {data?.level === 'STRONG' && (
          <FormField
            id="site-profile-confirmation"
            label={t('siteProfile.confirmLabel', { text: data.confirmation_text })}
            required
          >
            <InputText
              id="site-profile-confirmation"
              value={confirmation}
              autoComplete="off"
              onChange={(e) => setConfirmation(e.target.value)}
            />
          </FormField>
        )}
        {data?.level === 'BLOCKED' && (
          <>
            <Message severity="error" text={t('siteProfile.blockedHelp')} />
            {onCreateSite && target && (
              <Button
                type="button"
                icon="pi pi-plus"
                label={t('siteProfile.createSite')}
                outlined
                onClick={() => onCreateSite(target)}
              />
            )}
          </>
        )}
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          {data && data.level !== 'BLOCKED' && (
            <Button
              type="button"
              label={t('siteProfile.confirm')}
              severity={data.level === 'STRONG' ? 'danger' : undefined}
              disabled={!confirmed || preview.isFetching}
              loading={change.isPending}
              onClick={submit}
            />
          )}
        </div>
      </div>
    </Dialog>
  );
}
