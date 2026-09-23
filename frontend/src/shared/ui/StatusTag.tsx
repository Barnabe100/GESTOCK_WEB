import { Tag } from 'primereact/tag';
import { useTranslation } from 'react-i18next';

import type { SubscriptionStatus } from '@/core/api/types';

const SEVERITY: Record<SubscriptionStatus, 'success' | 'info' | 'warning' | 'danger'> = {
  trial: 'info',
  active: 'success',
  past_due: 'warning',
  expired: 'danger',
  suspended: 'danger',
  cancelled: 'danger',
};

export function SubscriptionStatusTag({ status }: { status: SubscriptionStatus }) {
  const { t } = useTranslation();
  return <Tag severity={SEVERITY[status]} value={t(`subscriptionStatus.${status}`)} />;
}
