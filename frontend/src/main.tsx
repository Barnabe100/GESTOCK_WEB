import { StrictMode, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';

import 'primereact/resources/themes/lara-light-blue/theme.css';
import 'primeicons/primeicons.css';
import './styles.css';
import '@/core/i18n';

const root = document.getElementById('root');
if (!root) throw new Error('Élément #root introuvable');

const render = (app: ReactNode) => createRoot(root).render(<StrictMode>{app}</StrictMode>);

// Console TechNova (ADR-0031) : application distincte, chargée à la demande ; l'application des
// entreprises n'en embarque pas le code (et inversement).
if (
  window.location.pathname === '/tech-admin' ||
  window.location.pathname.startsWith('/tech-admin/')
) {
  void import('@/console/ConsoleApp').then(({ ConsoleApp }) => render(<ConsoleApp />));
} else {
  void import('@/app/App').then(({ App }) => render(<App />));
}
