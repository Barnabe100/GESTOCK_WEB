import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { InputTextarea } from 'primereact/inputtextarea';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { FormField } from '@/shared/ui/FormField';
import { useToast } from '@/shared/ui/toast';

import { ORDER_CONFIRM, useOrderMutations, type Order } from './api';
import { orderError } from './ui';

/**
 * Commande reçue par QR Code « à confirmer » (R7, ADR-0052) : confirmer (elle suit ensuite le
 * circuit de toute commande) ou refuser avec un motif OBLIGATOIRE (définitif, affiché au
 * client). Boutons affichés avec `restaurant.orders.order.confirm` ; le serveur revérifie tout.
 */
export function QrDecisionActions({ order, compact = false }: { order: Order; compact?: boolean }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const { confirm, reject } = useOrderMutations();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  if (order.status !== 'PENDING_CONFIRMATION' || !can(ORDER_CONFIRM)) return null;
  const done = (message: string) => ({
    onSuccess: () => {
      toast.success(message);
      setRejecting(false);
      setReason('');
    },
    onError: (error: unknown) => toast.error(orderError(t, error)),
  });
  return (
    <>
      <Button
        icon="pi pi-check"
        label={t('restaurantOrders.qr.confirm')}
        size={compact ? 'small' : undefined}
        loading={confirm.isPending}
        aria-label={t('restaurantOrders.qr.confirmOrder', { number: order.daily_number })}
        onClick={() => confirm.mutate(order.id, done(t('restaurantOrders.qr.confirmed')))}
      />
      <Button
        icon="pi pi-times"
        label={t('restaurantOrders.qr.reject')}
        severity="danger"
        outlined
        size={compact ? 'small' : undefined}
        aria-label={t('restaurantOrders.qr.rejectOrder', { number: order.daily_number })}
        onClick={() => setRejecting(true)}
      />
      {rejecting && (
        <Dialog
          header={t('restaurantOrders.qr.rejectTitle', { number: order.daily_number })}
          visible
          onHide={() => setRejecting(false)}
          className="sm-dialog"
        >
          <div className="sm-form">
            <p>{t('restaurantOrders.qr.rejectHelp')}</p>
            <FormField id="qr-reject-reason" label={t('restaurantOrders.reason')} required>
              <InputTextarea
                id="qr-reject-reason"
                rows={2}
                maxLength={500}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                autoFocus
              />
            </FormField>
            <div className="sm-dialog-actions">
              <Button
                type="button"
                label={t('actions.cancel')}
                text
                onClick={() => setRejecting(false)}
              />
              <Button
                type="button"
                label={t('restaurantOrders.qr.reject')}
                severity="danger"
                disabled={reason.trim() === ''}
                loading={reject.isPending}
                onClick={() =>
                  reject.mutate(
                    { id: order.id, reason: reason.trim() },
                    done(t('restaurantOrders.qr.rejected')),
                  )
                }
              />
            </div>
          </div>
        </Dialog>
      )}
    </>
  );
}
