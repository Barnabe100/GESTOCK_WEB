import { useTranslation } from 'react-i18next';

import { translateError } from '@/shared/lib/errors';
import { useToast } from '@/shared/ui/toast';

import { useSetCustomerActive, type Customer } from './api';

/** Activation / désactivation (jamais de suppression), avec retour utilisateur. */
export function useCustomerStatus() {
  const { t } = useTranslation();
  const toast = useToast();
  const setActive = useSetCustomerActive();
  const toggle = (customer: Customer) =>
    setActive.mutate(
      { id: customer.id, active: !customer.is_active },
      {
        onSuccess: (saved) =>
          toast.success(t(saved.is_active ? 'customers.activated' : 'customers.deactivated')),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  return { toggle, pending: setActive.isPending };
}
