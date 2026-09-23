import type { i18n as I18n } from 'i18next';

import { resources } from './index';

/** Terminologie fournie par le profil d'activité : `{ "fr": { "catalog": { "item": "Produit" } } }`. */
export type Terminology = Record<string, Record<string, unknown>>;

/**
 * Applique la terminologie du profil sur l'espace de noms `terminology`.
 * Les valeurs par défaut sont d'abord restaurées (changement d'entreprise), puis surchargées.
 * Les composants n'ont jamais à connaître le secteur : ils utilisent des clés
 * (`terminology:catalog.items`) dont la valeur dépend du profil.
 */
export function applyTerminology(i18n: I18n, terminology: Terminology | undefined): void {
  for (const [lng, bundles] of Object.entries(resources)) {
    i18n.removeResourceBundle(lng, 'terminology');
    i18n.addResourceBundle(lng, 'terminology', structuredClone(bundles.terminology), true, true);
  }
  for (const [lng, overrides] of Object.entries(terminology ?? {})) {
    i18n.addResourceBundle(lng, 'terminology', overrides, true, true);
  }
}
