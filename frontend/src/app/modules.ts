import type { FrontendModule } from '@/core/modules/types';
import { alertsModule } from '@/modules/alerts';
import { auditModule } from '@/modules/audit';
import { catalogModule } from '@/modules/catalog';
import { dashboardModule } from '@/modules/dashboard';
import { organizationModule } from '@/modules/organization';
import { stockModule } from '@/modules/stock';
import { subscriptionModule } from '@/modules/subscription';
import { suppliersModule } from '@/modules/suppliers';
import { usersModule } from '@/modules/users';

/**
 * Registre explicite des modules frontend. Un module métier (POS, restaurant…) s'y ajoutera
 * lorsqu'il sera implémenté ; il n'est affiché que si le backend le déclare actif.
 */
export const FRONTEND_MODULES: readonly FrontendModule[] = [
  dashboardModule,
  catalogModule,
  suppliersModule,
  stockModule,
  alertsModule,
  organizationModule,
  usersModule,
  subscriptionModule,
  auditModule,
];
