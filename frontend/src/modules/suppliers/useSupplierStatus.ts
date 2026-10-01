import { useTranslation } from 'react-i18next';

import { translateError } from '@/shared/lib/errors';
import { confirmAction } from '@/shared/ui/confirm';
import { useToast } from '@/shared/ui/toast';

import { useSetSupplierActive, type Supplier } from './api';

/** Activation / désactivation (jamais de suppression, désactivation confirmée), avec retour. */
export function useSupplierStatus() {
  const { t } = useTranslation();
  const toast = useToast();
  const setActive = useSetSupplierActive();
  const run = (supplier: Supplier) =>
    setActive.mutate(
      { id: supplier.id, active: !supplier.is_active },
      {
        onSuccess: () => toast.success(t('suppliers.statusChanged')),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  // Désactivation : action sensible, confirmée ; réactivation directe.
  const toggle = (supplier: Supplier) => {
    if (!supplier.is_active) return run(supplier);
    confirmAction(t, {
      header: t('suppliers.deactivateTitle'),
      message: t('suppliers.deactivateConfirm', { name: supplier.name }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: () => run(supplier),
    });
  };
  return { toggle, pending: setActive.isPending };
}
