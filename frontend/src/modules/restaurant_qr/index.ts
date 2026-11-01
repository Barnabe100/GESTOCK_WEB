import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

import { POINT_VIEW } from './api';

/**
 * Commandes par QR Code (palier R7, ADR-0052) : administration des points QR et réglages du
 * site, affichées seulement si le backend déclare le module effectif sur le site. Les pages
 * PUBLIQUES (menu, commande, suivi) sont hors de l'application authentifiée (`/q`, `/q/track`).
 */
export const restaurantQrModule: FrontendModule = {
  code: 'restaurant.qr',
  navigation: [
    {
      key: 'restaurantQr',
      labelKey: 'nav.restaurantQr',
      group: 'restaurant',
      icon: 'pi pi-qrcode',
      path: '/restaurant/qr',
      permission: POINT_VIEW,
    },
  ],
  routes: [
    {
      path: 'restaurant/qr',
      component: lazy(() => import('./PointsPage')),
      permission: POINT_VIEW,
    },
  ],
};
