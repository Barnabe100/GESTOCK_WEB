import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

import type { Receipt } from './api';
import { DEFAULT_RECEIPT_FORMAT, type ReceiptFormat } from './formats';
import { SaleReceipt } from './SaleReceipt';

/**
 * Impression du navigateur limitée au reçu : le reçu est rendu hors de l'application
 * (`#sm-print-root`, invisible à l'écran), la règle `@page` du format n'est injectée que le temps
 * de l'impression, puis tout est retiré. Aucune dépendance à une imprimante particulière : toute
 * imprimante utilisable par le navigateur convient (ticket thermique 80 mm en V1).
 */
export function ReceiptPrinter({
  receipt,
  format = DEFAULT_RECEIPT_FORMAT,
  onDone,
}: {
  receipt: Receipt;
  format?: ReceiptFormat;
  onDone: () => void;
}) {
  const started = useRef(false);

  useEffect(() => {
    // Une seule impression par montage (double exécution des effets en développement).
    if (started.current) return;
    started.current = true;
    const page = document.createElement('style');
    page.dataset.receiptFormat = format.id;
    page.textContent = format.pageRule;
    document.head.append(page);
    document.body.classList.add('sm-printing');
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      page.remove();
      document.body.classList.remove('sm-printing');
      onDone();
    };
    window.addEventListener('afterprint', finish, { once: true });
    window.print();
    // `print()` rend la main une fois la boîte d'impression fermée (ou aussitôt si le
    // navigateur n'en affiche pas) : nettoyage même sans évènement `afterprint`.
    finish();
    return () => window.removeEventListener('afterprint', finish);
  }, [format, onDone]);

  return createPortal(
    <div id="sm-print-root">
      <SaleReceipt receipt={receipt} format={format} />
    </div>,
    document.body,
  );
}
