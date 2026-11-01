import { useTranslation } from 'react-i18next';

import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import type { Priority } from './api';

/** Priorité de la commande (K3) : badge seulement si elle n'est pas « normale ». */
const PRIORITY_TONES: Record<Priority, Tone> = {
  NORMAL: 'neutral',
  HIGH: 'warning',
  URGENT: 'danger',
};

export function PriorityBadge({ priority }: { priority: Priority }) {
  const { t } = useTranslation();
  if (priority === 'NORMAL') return null;
  return (
    <StatusBadge
      tone={PRIORITY_TONES[priority]}
      icon={priority === 'URGENT' ? 'pi pi-exclamation-triangle' : 'pi pi-arrow-up'}
      label={t(`restaurantKitchen.priorities.${priority}`)}
    />
  );
}
