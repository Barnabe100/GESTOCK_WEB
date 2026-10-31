import { Dropdown } from 'primereact/dropdown';
import { Message } from 'primereact/message';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { TABLE_VIEW, useBoard, useTables } from '@/modules/restaurant_tables/api';

import type { OrderTable } from './api';

/**
 * Tables des commandes (palier R5-C3). Le serveur fait foi : commande « sur place » seulement,
 * module des tables effectif, table du site et active ; plusieurs commandes peuvent partager une
 * table (A1 : simple avertissement). Lister les tables exige leur consultation : sans elle (ou
 * sans le module), aucune table ne peut être choisie, mais le retrait reste possible (B4).
 */

/** Une table peut être CHOISIE : module des tables présent et consultation des tables. */
export function useCanChooseTable(): boolean {
  const { can, hasModule } = useCapabilities();
  return hasModule('restaurant.tables') && can(TABLE_VIEW);
}

/** « T1 · Salle » : libellé et zone COURANTS (l'historique garde ceux d'alors). */
export function tableLabel(table: Pick<OrderTable, 'label' | 'zone_name'>): string {
  return table.zone_name ? `${table.label} · ${table.zone_name}` : table.label;
}

/** Tables ACTIVES du site, dans l'ordre du plan de salle. */
export function TablePicker({
  id,
  siteId,
  value,
  onChange,
  excludeId,
}: {
  id: string;
  siteId: string | null;
  value: string | null;
  onChange: (value: string | null) => void;
  excludeId?: string | null;
}) {
  const { t } = useTranslation();
  const query = new URLSearchParams({ status: 'active', limit: '100', sort: 'position' });
  if (siteId) query.set('site_id', siteId);
  const tables = useTables(query.toString(), siteId !== null);
  return (
    <Dropdown
      inputId={id}
      value={value}
      onChange={(e) => onChange((e.value as string | undefined) ?? null)}
      options={(tables.data?.items ?? [])
        .filter((table) => table.id !== excludeId)
        .map((table) => ({ value: table.id, label: tableLabel(table) }))}
      placeholder={t('restaurantOrders.chooseTable')}
      emptyMessage={t('restaurantOrders.noActiveTable')}
      showClear
      disabled={siteId === null}
    />
  );
}

/** Avertissement (non bloquant, A1) si la table choisie est déjà occupée selon le plan de salle. */
export function OccupiedTableWarning({
  siteId,
  tableId,
}: {
  siteId: string | null;
  tableId: string | null;
}) {
  const { t } = useTranslation();
  const board = useBoard(siteId, tableId !== null);
  const table = board.data?.tables.find((entry) => entry.id === tableId);
  if (!table || table.order_count === 0) return null;
  return (
    <Message
      severity="warn"
      text={t('restaurantOrders.tableOccupied', { count: table.order_count, label: table.label })}
    />
  );
}
