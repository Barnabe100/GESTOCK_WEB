import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import frCommon from './locales/fr/common.json';
import frConsole from './locales/fr/console.json';
import frErrors from './locales/fr/errors.json';
import frTerminology from './locales/fr/terminology.json';

export const DEFAULT_LANGUAGE = 'fr';
export const NAMESPACES = ['common', 'errors', 'terminology', 'console'] as const;

/**
 * Ressources par langue (référence immuable). Ajouter une langue = ajouter `locales/<lng>/`.
 * i18next modifie en place l'objet reçu à l'initialisation : on lui en passe une copie.
 */
export const resources = {
  fr: { common: frCommon, errors: frErrors, terminology: frTerminology, console: frConsole },
} as const;

void i18n.use(initReactI18next).init({
  resources: structuredClone(resources),
  lng: DEFAULT_LANGUAGE,
  fallbackLng: DEFAULT_LANGUAGE,
  ns: [...NAMESPACES],
  defaultNS: 'common',
  interpolation: { escapeValue: false },
  // Re-rendu des composants lorsque la terminologie du profil est appliquée.
  react: { bindI18nStore: 'added removed' },
  returnNull: false,
});

export default i18n;
