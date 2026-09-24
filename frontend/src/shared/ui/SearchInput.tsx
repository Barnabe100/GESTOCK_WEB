import { IconField } from 'primereact/iconfield';
import { InputIcon } from 'primereact/inputicon';
import { InputText } from 'primereact/inputtext';
import { useTranslation } from 'react-i18next';

export function SearchInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const { t } = useTranslation();
  return (
    <IconField iconPosition="left">
      <InputIcon className="pi pi-search" />
      <InputText
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? t('common.search')}
        aria-label={placeholder ?? t('common.search')}
      />
    </IconField>
  );
}
