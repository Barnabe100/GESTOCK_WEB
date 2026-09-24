import { Dropdown } from 'primereact/dropdown';
import { Tag } from 'primereact/tag';
import { useTranslation } from 'react-i18next';

import type { StatusFilterValue } from '@/shared/lib/serverTable';

export function StatusFilter({
  value,
  onChange,
}: {
  value: StatusFilterValue;
  onChange: (value: StatusFilterValue) => void;
}) {
  const { t } = useTranslation();
  return (
    <Dropdown
      value={value}
      onChange={(e) => onChange(e.value as StatusFilterValue)}
      options={(['all', 'active', 'inactive'] as const).map((v) => ({
        value: v,
        label: t(`statusFilter.${v}`),
      }))}
      aria-label={t('statusFilter.label')}
    />
  );
}

export function ActiveTag({ active }: { active: boolean }) {
  const { t } = useTranslation();
  return (
    <Tag
      severity={active ? 'success' : 'secondary'}
      value={t(active ? 'common.active' : 'common.inactive')}
    />
  );
}
