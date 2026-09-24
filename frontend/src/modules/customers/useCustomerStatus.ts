import { useTranslation } from 'react-i18next';

import { translateError } from '@/shared/lib/errors';
import { confirmAction } from '@/shared/ui/confirm';
import { useToast } from '@/shared/ui/toast';

import { useSetCustomerActive, type Customer } from './api';

/** Activation / désactivation (jamais de suppression, désactivation confirmée), avec retour. */
export function useCustomerStatus() {
  const { t } = useTranslation();
  const toast = useToast();
  const setActive = useSetCustomerActive();
  const run = (customer: Customer) =>
    setActive.mutate(
      { id: customer.id, active: !customer.is_active },
      {
        onSuccess: (saved) =>
          toast.success(t(saved.is_active ? 'customers.activated' : 'customers.deactivated')),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  // Désactivation : action sensible, confirmée ; réactivation directe.
  const toggle = (customer: Customer) => {
    if (!customer.is_active) return run(customer);
    confirmAction(t, {
      header: t('customers.deactivateTitle'),
      message: t('customers.deactivateConfirm', { name: customer.name }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: () => run(customer),
    });
  };
  return { toggle, pending: setActive.isPending };
}
