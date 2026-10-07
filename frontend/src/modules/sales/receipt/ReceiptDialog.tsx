import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Message } from 'primereact/message';
import { useCallback, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';

import {
  RECEIPT_PRINT,
  RECEIPT_REPRINT,
  usePrintReceipt,
  useSaleReceipt,
  type Receipt,
} from './api';
import { ReceiptPrinter } from './ReceiptPrinter';
import { SaleReceipt } from './SaleReceipt';

/**
 * Impression d'un reçu : le serveur autorise et journalise l'impression (première impression :
 * `receipt_print`, suivantes : `reprint`) et renvoie le reçu à imprimer, qui part ensuite à
 * l'imprimante du navigateur. `printLabel` : « Imprimer » ou « Réimprimer » selon les
 * impressions déjà journalisées.
 */
export function useReceiptPrinting() {
  const { t } = useTranslation();
  const { can } = useCapabilities();
  const mutation = usePrintReceipt();
  const [printing, setPrinting] = useState<Receipt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const done = useCallback(() => setPrinting(null), []);

  const print = (saleId: string) => {
    setError(null);
    mutation.mutate(saleId, {
      onSuccess: (receipt) => setPrinting(receipt),
      onError: (failure) => setError(translateError(t, failure)),
    });
  };
  /** Bouton proposé selon les permissions ; le serveur refait le contrôle de toute façon. */
  const allowed = (printCount: number) => can(printCount > 0 ? RECEIPT_REPRINT : RECEIPT_PRINT);
  const printLabel = (printCount: number) =>
    t(printCount > 0 ? 'receipt.reprint' : 'receipt.print');
  const printer: ReactNode = printing ? <ReceiptPrinter receipt={printing} onDone={done} /> : null;

  return { print, allowed, printLabel, printer, pending: mutation.isPending, error };
}

/** Aperçu du reçu d'une vente persistée (format V1 : 80 mm), avec impression / réimpression. */
export function ReceiptDialog({ saleId, onClose }: { saleId: string; onClose: () => void }) {
  const { t } = useTranslation();
  const receipt = useSaleReceipt(saleId);
  const printing = useReceiptPrinting();
  const count = receipt.data?.print_count ?? 0;

  return (
    <Dialog
      header={t('receipt.dialogTitle')}
      visible
      onHide={onClose}
      className="sm-dialog sm-receipt-dialog"
    >
      {receipt.isPending ? (
        <LoadingState />
      ) : receipt.isError ? (
        <ErrorMessage error={receipt.error} onRetry={() => void receipt.refetch()} />
      ) : (
        <div className="sm-form">
          <div className="sm-receipt-preview">
            <SaleReceipt receipt={receipt.data} />
          </div>
          <p className="sm-help" data-testid="receipt-print-count">
            {count > 0 ? t('receipt.printedCount', { count }) : t('receipt.neverPrinted')}
          </p>
          {printing.error && <Message severity="error" text={printing.error} />}
          <div className="sm-dialog-actions">
            <Button type="button" label={t('actions.close')} text onClick={onClose} />
            {printing.allowed(count) && (
              <Button
                type="button"
                icon="pi pi-print"
                label={printing.printLabel(count)}
                loading={printing.pending}
                onClick={() => printing.print(saleId)}
              />
            )}
          </div>
        </div>
      )}
      {printing.printer}
    </Dialog>
  );
}

/**
 * Actions du reçu sur la fiche d'une vente VALIDÉE (historique) : « Voir le reçu » et
 * « Imprimer » / « Réimprimer » selon les impressions déjà journalisées et les permissions
 * (le serveur refait le contrôle et journalise l'impression).
 */
export function SaleReceiptActions({ saleId }: { saleId: string }) {
  const { t } = useTranslation();
  const receipt = useSaleReceipt(saleId);
  const printing = useReceiptPrinting();
  const [viewing, setViewing] = useState(false);
  const count = receipt.data?.print_count ?? 0;

  return (
    <>
      <Button
        type="button"
        icon="pi pi-receipt"
        label={t('receipt.view')}
        outlined
        onClick={() => setViewing(true)}
      />
      {receipt.data && printing.allowed(count) && (
        <Button
          type="button"
          icon="pi pi-print"
          label={printing.printLabel(count)}
          outlined
          loading={printing.pending}
          onClick={() => printing.print(saleId)}
        />
      )}
      {printing.error && <Message severity="error" text={printing.error} />}
      {viewing && <ReceiptDialog saleId={saleId} onClose={() => setViewing(false)} />}
      {printing.printer}
    </>
  );
}
