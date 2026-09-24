// Données de test partagées (importées par les tests seuls).
import type { Inventory, InventoryLine, InventorySummary } from './api';

export const summary = (over: Partial<InventorySummary> = {}): InventorySummary => ({
  lines: 2,
  counted: 1,
  surplus: 0,
  shortage: 1,
  no_variance: 0,
  surplus_value: '0.00',
  shortage_value: '5000.00',
  adjustment_value: '-5000.00',
  final: false,
  ...over,
});

export const inventory = (over: Partial<Inventory> = {}): Inventory => ({
  id: 'i1',
  number: 'INV-000001',
  site_id: 's1',
  site_name: 'Boutique',
  status: 'COUNTING',
  inventory_type: 'TARGETED',
  comment: null,
  line_count: 2,
  counted_count: 1,
  variance_count: 1,
  created_at: '2026-09-24T08:00:00Z',
  updated_at: '2026-09-24T08:00:00Z',
  created_by_name: 'Awa',
  started_at: '2026-09-24T08:05:00Z',
  started_by_name: 'Awa',
  completed_at: null,
  completed_by_name: null,
  validated_at: null,
  validated_by_name: null,
  cancelled_at: null,
  cancelled_by_name: null,
  cancellation_reason: null,
  summary: summary(),
  ...over,
});

export const line = (over: Partial<InventoryLine> = {}): InventoryLine => ({
  id: 'l1',
  article_id: 'a1',
  reference: 'RIZ-25',
  designation: 'Riz 25 kg',
  unit: 'sac',
  category_name: 'Épicerie',
  article_active: true,
  stock_theoretical_initial: '100.000',
  stock_current: '105.000',
  stock_theoretical_at_validation: null,
  quantity_physical: '95.000',
  indicative_variance: '-5.000',
  quantity_variance: '-10.000',
  unit_cost: null,
  adjustment_value: '-10000.00',
  counted_at: '2026-09-24T08:10:00Z',
  counted_by_name: 'Awa',
  ...over,
});

export const uncounted = line({
  id: 'l2',
  article_id: 'a2',
  reference: 'HUILE-5',
  designation: 'Huile 5 L',
  unit: 'bidon',
  stock_theoretical_initial: '12.000',
  stock_current: '12.000',
  quantity_physical: null,
  indicative_variance: null,
  quantity_variance: null,
  adjustment_value: null,
  counted_at: null,
  counted_by_name: null,
});

export const ALL_PERMISSIONS = [
  'inventory_count.inventory.view',
  'inventory_count.inventory.create',
  'inventory_count.inventory.update',
  'inventory_count.inventory.count',
  'inventory_count.inventory.validate',
  'inventory_count.inventory.cancel',
];
