/**
 * Formats d'impression du reçu. V1 : un seul format implémenté, le ticket thermique 80 mm
 * (impression du navigateur, aucune dépendance à une imprimante). Le contenu du reçu
 * (`SaleReceipt`) ne dépend pas du format : un format = une largeur de page, une classe CSS
 * de mise en page et la règle `@page` injectée le temps de l'impression. Ajouter plus tard
 * THERMAL_58 ou A4 = une entrée ici et ses règles CSS (`.sm-receipt--…`), sans toucher au
 * contenu ni au flux d'impression.
 */
export type ReceiptFormatId = 'THERMAL_80';

export interface ReceiptFormat {
  id: ReceiptFormatId;
  /** Largeur du support imprimé (mm). */
  widthMm: number;
  /** Modificateur CSS du reçu (`sm-receipt--thermal-80`). */
  className: string;
  /** Règle de page appliquée uniquement pendant l'impression du reçu. */
  pageRule: string;
}

export const RECEIPT_FORMATS: Record<ReceiptFormatId, ReceiptFormat> = {
  THERMAL_80: {
    id: 'THERMAL_80',
    widthMm: 80,
    className: 'sm-receipt--thermal-80',
    pageRule: '@page { size: 80mm auto; margin: 0; }',
  },
};

/** Format standard du reçu POS en V1. */
export const DEFAULT_RECEIPT_FORMAT: ReceiptFormat = RECEIPT_FORMATS.THERMAL_80;
