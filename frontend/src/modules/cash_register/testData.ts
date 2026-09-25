import type { CashMovement, CashRegister, CashSession } from './api';

export const MANAGER = [
  'cash_register.register.view',
  'cash_register.register.manage',
  'cash_register.session.view',
  'cash_register.session.open',
  'cash_register.session.close',
  'cash_register.movement.create',
  'sales.sale.view',
];
export const SELLER = [
  'cash_register.register.view',
  'cash_register.session.view',
  'cash_register.session.open',
  'cash_register.session.close',
];
export const VIEWER = ['cash_register.register.view', 'cash_register.session.view'];

export function cashSession(over: Partial<CashSession> = {}): CashSession {
  return {
    id: 'cs1',
    number: 'SES-000001',
    cash_register_id: 'r1',
    cash_register_code: 'CAI-001',
    cash_register_name: 'Caisse principale',
    site_id: 's1',
    site_name: 'Boutique',
    status: 'OPEN',
    opening_float: '100000.00',
    opened_at: '2026-09-25T08:02:00Z',
    opened_by_name: 'Aïcha',
    closed_at: null,
    closed_by_name: null,
    cash_in_total: '125000.00',
    cash_out_total: '70000.00',
    theoretical_balance: '155000.00',
    counted_balance: null,
    variance: null,
    closing_note: null,
    movement_count: 5,
    ...over,
  };
}

export function register(over: Partial<CashRegister> = {}): CashRegister {
  return {
    id: 'r1',
    code: 'CAI-001',
    name: 'Caisse principale',
    description: null,
    site_id: 's1',
    site_name: 'Boutique',
    is_active: true,
    created_at: '2026-09-25T07:00:00Z',
    updated_at: '2026-09-25T07:00:00Z',
    current_session: {
      id: 'cs1',
      number: 'SES-000001',
      opened_at: '2026-09-25T08:02:00Z',
      opened_by_name: 'Aïcha',
      opening_float: '100000.00',
      theoretical_balance: '155000.00',
    },
    ...over,
  };
}

export function movement(over: Partial<CashMovement> = {}): CashMovement {
  return {
    id: 'm1',
    cash_session_id: 'cs1',
    cash_session_number: 'SES-000001',
    cash_register_id: 'r1',
    cash_register_code: 'CAI-001',
    cash_register_name: 'Caisse principale',
    site_id: 's1',
    site_name: 'Boutique',
    movement_type: 'SALE_CASH_IN',
    amount: '50000.00',
    signed_amount: '50000.00',
    category: null,
    reason: null,
    reference: 'PAY-000001',
    source_type: 'sale',
    source_id: 'v1',
    source_number: 'VTE-000001',
    payment_id: 'p1',
    occurred_at: '2026-09-25T09:00:00Z',
    created_by_name: 'Aïcha',
    balance_after: '150000.00',
    ...over,
  };
}
