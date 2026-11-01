import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PublicMenuPage } from './PublicMenuPage';
import { PublicTrackingPage } from './PublicTrackingPage';
import { applyPublicPagePolicies, takeFragmentSecret } from './secret';

/**
 * Application PUBLIQUE des commandes par QR Code (R7, ADR-0052), chargée à la demande pour
 * `/q` (menu et commande) et `/q/track` (suivi) : distincte de l'application des entreprises
 * (aucune session, aucun appel d'authentification, aucune donnée du personnel).
 */

const TOKEN_KEY = 'sm.qr.token';
const TRACKING_KEY = 'sm.qr.tracking';

export function isPublicQrPath(pathname: string): boolean {
  return pathname === '/q' || pathname === '/q/' || pathname === '/q/track';
}

export function PublicQrApp() {
  const { t } = useTranslation();
  const tracking = window.location.pathname === '/q/track';
  // AVANT le premier affichage et toute requête : politiques de la page, puis secret lu et
  // retiré aussitôt de l'adresse (une seconde exécution relit l'onglet).
  const storageKey = tracking ? TRACKING_KEY : TOKEN_KEY;
  const [secret, setSecret] = useState<string | null>(() => {
    applyPublicPagePolicies();
    return takeFragmentSecret(storageKey);
  });
  // Nouveau QR scanné dans le MÊME onglet : seul le fragment change (aucun rechargement) — le
  // nouveau secret remplace l'ancien, lu puis effacé de l'adresse comme au premier affichage.
  useEffect(() => {
    const onHashChange = () => {
      if (window.location.hash !== '') setSecret(takeFragmentSecret(storageKey));
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [storageKey]);
  if (secret === null) {
    return (
      <main className="sm-qr-public">
        <p>{t(tracking ? 'errors:qr_tracking_unavailable' : 'errors:qr_unavailable')}</p>
      </main>
    );
  }
  // Une clé par secret : changer de QR repart d'un panier vide.
  return tracking ? (
    <PublicTrackingPage key={secret} secret={secret} />
  ) : (
    <PublicMenuPage key={secret} token={secret} />
  );
}
