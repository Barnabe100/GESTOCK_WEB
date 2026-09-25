import type { FrontendModule } from '@/core/modules/types';
import { alertsModule } from '@/modules/alerts';
import { auditModule } from '@/modules/audit';
import { cashRegisterModule } from '@/modules/cash_register';
import { catalogModule } from '@/modules/catalog';
import { customersModule } from '@/modules/customers';
import { dashboardModule } from '@/modules/dashboard';
import { inventoryCountModule } from '@/modules/inventory_count';
import { organizationModule } from '@/modules/organization';
import { receivablesModule } from '@/modules/receivables';
import { salesModule } from '@/modules/sales';
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
  customersModule,
  salesModule,
  receivablesModule,
  cashRegisterModule,
  stockModule,
  inventoryCountModule,
  alertsModule,
  organizationModule,
  usersModule,
  subscriptionModule,
  auditModule,
];
