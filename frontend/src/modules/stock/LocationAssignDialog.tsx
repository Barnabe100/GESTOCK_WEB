import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import { useAssignLocation, useStockLevels, useStockLocations, type StockLocation } from './api';

/** Emplacement affiché d'un article sur un site : nom (inactif signalé) ou « Non rangé ». */
export function LocationLabel({
  name,
  active,
}: {
  name: string | null | undefined;
  active?: boolean | null;
}) {
  const { t } = useTranslation();
  if (!name) return <span className="sm-muted">{t('locations.unlocated')}</span>;
  return (
    <span className="sm-tags">
      <span>{name}</span>
      {active === false && <StatusBadge tone="neutral" label={t('common.inactive')} />}
    </span>
  );
}

/**
 * Emplacement COURANT d'un article sur UN site (Lot 3-F) : choix parmi les emplacements ACTIFS
 * de ce site seulement, ou « Non rangé ». Le serveur revérifie le site, l'emplacement et la
 * permission ; aucun effet sur le stock.
 */
export function LocationAssignDialog({
  siteId,
  siteName,
  articleId,
  articleLabel,
  currentId,
  currentName,
  onClose,
}: {
  siteId: string;
  siteName: string;
  articleId: string;
  articleLabel: string;
  currentId: string | null;
  currentName?: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const assign = useAssignLocation();
  const locations = useStockLocations(
    new URLSearchParams({
      site_id: siteId,
      status: 'active',
      limit: '200',
      sort: 'name',
    }).toString(),
  );
  const [value, setValue] = useState<string | null>(currentId);
  const options = (locations.data?.items ?? []).map((l: StockLocation) => ({
    value: l.id,
    label: l.name,
  }));
  // Emplacement courant devenu inactif : affiché (conservé), mais non proposé à nouveau.
  if (currentId && !options.some((o) => o.value === currentId)) {
    options.unshift({
      value: currentId,
      label: `${currentName ?? '…'} (${t('common.inactive').toLowerCase()})`,
    });
  }

  const save = () =>
    assign.mutate(
      { siteId, articleId, locationId: value },
      {
        onSuccess: () => {
          toast.success(t('locations.assigned'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );

  return (
    <Dialog
      header={`${t('locations.assignTitle')} — ${articleLabel} · ${siteName}`}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <div className="sm-form">
        <p className="sm-help">{t('locations.assignHelp')}</p>
        <FormField id="assign-location" label={t('locations.location')}>
          <Dropdown
            inputId="assign-location"
            value={value}
            onChange={(e) => setValue((e.value as string | undefined) ?? null)}
            options={options}
            placeholder={t('locations.unlocated')}
            emptyMessage={t('locations.noneOnSite')}
            showClear
            filter
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="button"
            label={t('actions.save')}
            loading={assign.isPending}
            onClick={save}
          />
        </div>
      </div>
    </Dialog>
  );
}

/**
 * Emplacement COURANT d'un article sur un site, en aide à la saisie d'une entrée ou d'une
 * sortie (Lot 3-F) : indicatif seulement, jamais bloquant ni figé dans le document. Affiché
 * avec la consultation du stock (`stock.level.view`).
 */
export function ArticleLocationHint({ siteId, articleId }: { siteId: string; articleId: string }) {
  const { t } = useTranslation();
  const { can } = useCapabilities();
  const visible = can('stock.level.view');
  const levels = useStockLevels(
    new URLSearchParams({
      site_id: siteId,
      article_id: articleId,
      include_inactive: 'true',
      limit: '1',
    }).toString(),
    visible,
  );
  const level = levels.data?.items[0];
  if (!visible || !level) return null;
  return (
    <small className="sm-help">
      {t('locations.location')} :{' '}
      <LocationLabel name={level.location_name} active={level.location_active} />
    </small>
  );
}
