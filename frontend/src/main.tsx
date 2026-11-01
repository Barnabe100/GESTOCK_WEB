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
const { pathname } = window.location;
if (pathname === '/tech-admin' || pathname.startsWith('/tech-admin/')) {
  void import('@/console/ConsoleApp').then(({ ConsoleApp }) => render(<ConsoleApp />));
} else if (pathname === '/q' || pathname === '/q/' || pathname === '/q/track') {
  // Commandes par QR Code (R7, ADR-0052) : pages PUBLIQUES distinctes, sans session.
  void import('@/modules/restaurant_qr/public/PublicQrApp').then(({ PublicQrApp }) =>
    render(<PublicQrApp />),
  );
} else {
  void import('@/app/App').then(({ App }) => render(<App />));
}
