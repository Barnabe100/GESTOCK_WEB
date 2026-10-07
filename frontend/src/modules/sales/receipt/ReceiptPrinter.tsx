import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

import type { Receipt } from './api';
import { DEFAULT_RECEIPT_FORMAT, type ReceiptFormat } from './formats';
import { SaleReceipt } from './SaleReceipt';

/** Attente maximale des images du reçu (logo) : une image lente ou cassée ne bloque jamais. */
const IMAGE_WAIT_MS = 3000;

/** Images du reçu chargées (ou en erreur) : le navigateur imprime l'état affiché. */
function imagesReady(root: HTMLElement | null): Promise<void> {
  const pending = [...(root?.querySelectorAll('img') ?? [])]
    .filter((img) => !img.complete || img.naturalWidth === 0)
    .map(
      (img) =>
        new Promise<void>((resolve) => {
          img.addEventListener('load', () => resolve(), { once: true });
          img.addEventListener('error', () => resolve(), { once: true });
        }),
    );
  if (pending.length === 0) return Promise.resolve();
  return Promise.race([
    Promise.all(pending).then(() => undefined),
    new Promise<void>((resolve) => setTimeout(resolve, IMAGE_WAIT_MS)),
  ]);
}

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
  const printed = useRef(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Une seule impression par montage (double exécution des effets en développement : la
    // première attente est annulée, la seconde imprime).
    if (printed.current) return;
    let cancelled = false;
    const printNow = () => {
      const page = document.createElement('style');
      page.dataset.receiptFormat = format.id;
      page.textContent = format.pageRule;
      document.head.append(page);
      document.body.classList.add('sm-printing');
      let finished = false;
      const finish = () => {
        if (finished) return;
        finished = true;
        window.removeEventListener('afterprint', finish);
        page.remove();
        document.body.classList.remove('sm-printing');
        onDone();
      };
      window.addEventListener('afterprint', finish, { once: true });
      window.print();
      // `print()` rend la main une fois la boîte d'impression fermée (ou aussitôt si le
      // navigateur n'en affiche pas) : nettoyage même sans évènement `afterprint`.
      finish();
    };
    // Logo du tenant : une image tout juste insérée n'est pas encore chargée ; imprimer
    // aussitôt donnerait un ticket sans logo.
    void imagesReady(root.current).then(() => {
      if (cancelled || printed.current) return;
      printed.current = true;
      printNow();
    });
    return () => {
      cancelled = true;
    };
  }, [format, onDone]);

  return createPortal(
    <div id="sm-print-root" ref={root}>
      <SaleReceipt receipt={receipt} format={format} />
    </div>,
    document.body,
  );
}
