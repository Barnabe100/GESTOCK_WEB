import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCustomers, type Customer } from '@/modules/customers/api';
import { useDebouncedValue } from '@/shared/lib/serverTable';
import { EmptyState } from '@/shared/ui/EmptyState';
import { LoadingState } from '@/shared/ui/LoadingState';
import { SearchInput } from '@/shared/ui/SearchInput';
import { StatusBadge } from '@/shared/ui/StatusBadge';

/**
 * Choix du client (F4) : recherche serveur (code, nom, téléphone). Un client désactivé est
 * affiché mais ne peut pas être choisi (règle des ventes, contrôlée aussi par le serveur).
 */
export function PosCustomerDialog({
  onSelect,
  onClose,
}: {
  onSelect: (customer: Customer | null) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const debounced = useDebouncedValue(search);
  const customers = useCustomers(
    new URLSearchParams({ search: debounced, limit: '8', sort: 'name' }).toString(),
  );
  const items = customers.data?.items ?? [];

  return (
    <Dialog header={t('pos.chooseCustomer')} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        <SearchInput value={search} placeholder={t('pos.customerSearch')} onChange={setSearch} />
        {customers.isPending ? (
          <LoadingState />
        ) : items.length === 0 ? (
          <EmptyState icon="pi pi-users" title={t('common.noResults')} />
        ) : (
          <ul className="sm-pos-choices" aria-label={t('pos.customers')}>
            {items.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className="sm-pos-choice"
                  disabled={!c.is_active}
                  onClick={() => onSelect(c)}
                >
                  <span className="sm-pos-choice-main">
                    <strong>{c.name}</strong>
                    <span className="sm-muted">
                      {[c.code, t(`customers.types.${c.customer_type}`), c.phone]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </span>
                  {!c.is_active && <StatusBadge tone="neutral" label={t('common.inactive')} />}
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="button"
            icon="pi pi-user-minus"
            label={t('pos.noCustomer')}
            outlined
            onClick={() => onSelect(null)}
          />
        </div>
      </div>
    </Dialog>
  );
}
