import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputTextarea } from 'primereact/inputtextarea';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import {
  KITCHEN_MODULE,
  LINE_ROUTE,
  PRIORITIES,
  PRIORITY_SET,
  STATION_VIEW,
  useKitchenMutations,
  useStations,
  type Priority,
} from '@/modules/restaurant_kitchen/api';
import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';
import { useToast } from '@/shared/ui/toast';

import type { Order, OrderLine } from './api';

/**
 * Cuisine sur la fiche d'une commande (palier R6, ADR-0050) : poste de chaque ligne, priorité,
 * correction manuelle du poste. Proposé seulement si le module Cuisine est effectif sur le site
 * et avec la permission ; le serveur revérifie tout (site, état des lignes, poste actif).
 */
export function useKitchenOnOrder() {
  const { can, hasModule } = useCapabilities();
  const effective = hasModule(KITCHEN_MODULE);
  return {
    effective,
    canRoute: effective && can(LINE_ROUTE) && can(STATION_VIEW),
    canPrioritize: effective && can(PRIORITY_SET),
  };
}

function ReasonField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation();
  return (
    <FormField id="kitchen-reason" label={t('restaurantOrders.reason')}>
      <InputTextarea
        id="kitchen-reason"
        rows={2}
        maxLength={500}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </FormField>
  );
}

/** Poste des lignes choisies : postes ACTIFS du site de la commande, ou « Sans poste ». */
export function RouteLinesDialog({
  order,
  lines,
  onClose,
}: {
  order: Order;
  lines: OrderLine[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const query = new URLSearchParams({
    site_id: order.site_id,
    status: 'active',
    sort: 'position',
    limit: '100',
  }).toString();
  const stations = useStations(query);
  const current = lines.length === 1 ? (lines[0]?.station_id ?? null) : null;
  const [station, setStation] = useState<string>(current ?? 'none');
  const [reason, setReason] = useState('');
  const { routeLines } = useKitchenMutations();
  const name = lines.length === 1 ? (lines[0]?.label ?? '') : '';
  return (
    <Dialog
      header={
        name ? t('restaurantKitchen.stationOfLine', { name }) : t('restaurantKitchen.changeStation')
      }
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <div className="sm-form">
        <p className="sm-help">{t('restaurantKitchen.changeStationHelp')}</p>
        <FormField id="kitchen-line-station" label={t('restaurantKitchen.newStationFor')} required>
          <Dropdown
            inputId="kitchen-line-station"
            value={station}
            onChange={(e) => setStation(e.value as string)}
            options={[
              ...(stations.data?.items ?? []).map((s) => ({ value: s.id, label: s.name })),
              { value: 'none', label: t('restaurantKitchen.noStation') },
            ]}
          />
        </FormField>
        <ReasonField value={reason} onChange={setReason} />
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="button"
            label={t('restaurantKitchen.changeStation')}
            loading={routeLines.isPending}
            onClick={() =>
              routeLines.mutate(
                {
                  id: order.id,
                  lineIds: lines.map((l) => l.id),
                  stationId: station === 'none' ? null : station,
                  reason: reason.trim() === '' ? null : reason.trim(),
                },
                {
                  onSuccess: () => {
                    toast.success(t('restaurantKitchen.lineRouted'));
                    onClose();
                  },
                  onError: (error) => toast.error(translateError(t, error)),
                },
              )
            }
          />
        </div>
      </div>
    </Dialog>
  );
}

/** Priorité de la commande (K3) : normale, élevée, urgente ; motif facultatif, journalisé. */
export function PriorityDialog({ order, onClose }: { order: Order; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const [priority, setPriority] = useState<Priority>(order.priority ?? 'NORMAL');
  const [reason, setReason] = useState('');
  const mutations = useKitchenMutations();
  return (
    <Dialog
      header={t('restaurantKitchen.changePriority')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <div className="sm-form">
        <p className="sm-help">{t('restaurantKitchen.priorityHelp')}</p>
        <FormField id="kitchen-priority" label={t('restaurantKitchen.priority')} required>
          <Dropdown
            inputId="kitchen-priority"
            value={priority}
            onChange={(e) => setPriority(e.value as Priority)}
            options={PRIORITIES.map((p) => ({
              value: p,
              label: t(`restaurantKitchen.priorities.${p}`),
            }))}
          />
        </FormField>
        <ReasonField value={reason} onChange={setReason} />
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="button"
            label={t('actions.save')}
            loading={mutations.setPriority.isPending}
            onClick={() =>
              mutations.setPriority.mutate(
                {
                  id: order.id,
                  priority,
                  reason: reason.trim() === '' ? undefined : reason.trim(),
                },
                {
                  onSuccess: () => {
                    toast.success(t('restaurantKitchen.prioritySaved'));
                    onClose();
                  },
                  onError: (error) => toast.error(translateError(t, error)),
                },
              )
            }
          />
        </div>
      </div>
    </Dialog>
  );
}
