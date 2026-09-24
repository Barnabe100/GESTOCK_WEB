import { AutoComplete } from 'primereact/autocomplete';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '@/core/api/client';
import type { Customer } from '@/modules/customers/api';
import type { Page } from '@/shared/lib/serverTable';

export interface CustomerOption {
  id: string;
  code: string;
  name: string;
  label: string;
}

export function toCustomerOption(c: { id: string; code: string; name: string }): CustomerOption {
  return { id: c.id, code: c.code, name: c.name, label: `${c.name} (${c.code})` };
}

/**
 * Recherche serveur des clients ACTIFS (code, nom, téléphone) ; vide = vente anonyme.
 * `includeInactive` : clients désactivés compris (filtres de consultation, ex. créances).
 */
export function CustomerPicker({
  id,
  value,
  onChange,
  includeInactive = false,
  placeholder,
  ariaLabel,
  className = 'sm-article-picker',
}: {
  id: string;
  value: CustomerOption | null;
  onChange: (value: CustomerOption | null) => void;
  includeInactive?: boolean;
  placeholder?: string;
  ariaLabel?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const [suggestions, setSuggestions] = useState<CustomerOption[]>([]);
  const [text, setText] = useState<string | null>(null);

  const complete = async (query: string) => {
    const params = new URLSearchParams({
      search: query,
      status: includeInactive ? 'all' : 'active',
      limit: '20',
    });
    try {
      const page = await api.get<Page<Customer>>(`/customers?${params.toString()}`);
      setSuggestions(page.items.map(toCustomerOption));
    } catch {
      setSuggestions([]);
    }
  };

  return (
    <AutoComplete
      inputId={id}
      value={text ?? value}
      field="label"
      suggestions={suggestions}
      completeMethod={(e) => void complete(e.query)}
      onChange={(e) => {
        if (typeof e.value === 'string') {
          setText(e.value);
          if (value) onChange(null);
        } else {
          setText(null);
          onChange((e.value as CustomerOption | null) ?? null);
        }
      }}
      placeholder={placeholder ?? t('sales.anonymous')}
      aria-label={ariaLabel}
      forceSelection
      dropdown
      className={className}
    />
  );
}
