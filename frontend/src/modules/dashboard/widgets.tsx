import type { ComponentType } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { useReceivableSummary } from '@/modules/receivables/api';
import { formatMoney } from '@/shared/lib/decimal';
import { MetricCard } from '@/shared/ui/MetricCard';

import { useAlertSummary, useDocumentCount } from './api';
import { RecentSalesPanel } from './RecentSalesPanel';

/**
 * Registre des widgets et raccourcis du tableau de bord (Phase 3.1, ADR-0024).
 *
 * Le profil UX choisit lesquels afficher et dans quel ordre (`caps.ux.dashboard`, identifiants
 * `<module>:<id>`, déjà restreints par le backend aux modules effectifs) ; chaque élément
 * déclare en plus la permission et la fonctionnalité de plan requises. Un identifiant déclaré
 * par un profil mais absent d'ici (module futur : tables, cuisine…) n'est simplement pas
 * affiché. Aucun widget ne teste le secteur ou le profil.
 */
export interface DashboardWidget {
  id: string;
  /** `metric` : indicateur de la bande du haut ; `panel` : carte de la colonne principale. */
  kind: 'metric' | 'panel';
  permission?: string;
  feature?: string;
  component: ComponentType;
}

export interface DashboardShortcut {
  id: string;
  labelKey: string;
  icon: string;
  to: string;
  permission?: string;
  feature?: string;
}

/** Valeur d'un indicateur : tiret pendant le chargement ou en cas d'erreur. */
function metricValue(value: number | undefined): string | number {
  return value ?? '—';
}

/** Date du jour dans le fuseau du tenant (AAAA-MM-JJ), pour les indicateurs « du jour ». */
export function tenantToday(timeZone: string, now = new Date()): string {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone }).format(now);
  } catch {
    return now.toISOString().slice(0, 10);
  }
}

function OutOfStock() {
  const { t } = useTranslation();
  const out = useAlertSummary(true).data?.out;
  return (
    <MetricCard
      icon="pi pi-times-circle"
      tone={out ? 'danger' : 'success'}
      value={metricValue(out)}
      label={t('dashboard.outOfStock')}
      hint={t('dashboard.seeAlerts')}
      to="/alerts/stock"
    />
  );
}

function LowStock() {
  const { t } = useTranslation();
  const low = useAlertSummary(true).data?.low;
  return (
    <MetricCard
      icon="pi pi-exclamation-triangle"
      tone={low ? 'warning' : 'success'}
      value={metricValue(low)}
      label={t('dashboard.lowStock')}
      hint={t('dashboard.seeAlerts')}
      to="/alerts/stock"
    />
  );
}

function SalesToday() {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const today = tenantToday(capabilities.tenant.timezone);
  const count = useDocumentCount(
    '/sales',
    `status=VALIDATED&date_from=${today}&date_to=${today}`,
    true,
  );
  return (
    <MetricCard
      icon="pi pi-chart-line"
      tone="info"
      value={metricValue(count.data)}
      label={t('dashboard.salesToday')}
      to="/sales"
    />
  );
}

function DraftSales() {
  const { t } = useTranslation();
  const count = useDocumentCount('/sales', 'status=DRAFT', true);
  return (
    <MetricCard
      icon="pi pi-file-edit"
      tone="info"
      value={metricValue(count.data)}
      label={t('dashboard.draftSales')}
      to="/sales"
    />
  );
}

function DraftTransfers() {
  const { t } = useTranslation();
  const count = useDocumentCount('/stock/transfers', 'status=DRAFT', true);
  return (
    <MetricCard
      icon="pi pi-arrow-right-arrow-left"
      tone="info"
      value={metricValue(count.data)}
      label={t('dashboard.draftTransfers')}
      to="/stock/transfers"
    />
  );
}

function OutstandingReceivables() {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const summary = useReceivableSummary('').data;
  return (
    <MetricCard
      icon="pi pi-wallet"
      tone={summary && summary.receivables_count > 0 ? 'warning' : 'success'}
      value={
        summary
          ? formatMoney(
              summary.total_receivables,
              capabilities.tenant.currency,
              capabilities.tenant.locale,
            )
          : '—'
      }
      label={t('dashboard.receivables')}
      hint={summary ? t('dashboard.debtors', { count: summary.debtor_customers_count }) : undefined}
      to="/receivables"
    />
  );
}

function OpenCashSessions() {
  const { t } = useTranslation();
  const count = useDocumentCount('/cash/sessions', 'status=OPEN', true);
  return (
    <MetricCard
      icon="pi pi-inbox"
      tone={count.data ? 'success' : 'neutral'}
      value={metricValue(count.data)}
      label={t('dashboard.openCashSessions')}
      to="/cash/sessions"
    />
  );
}

export const DASHBOARD_WIDGETS: readonly DashboardWidget[] = [
  {
    id: 'alerts:out_of_stock',
    kind: 'metric',
    permission: 'alerts.stock.view',
    component: OutOfStock,
  },
  { id: 'alerts:low_stock', kind: 'metric', permission: 'alerts.stock.view', component: LowStock },
  { id: 'sales:today', kind: 'metric', permission: 'sales.sale.view', component: SalesToday },
  { id: 'sales:drafts', kind: 'metric', permission: 'sales.sale.view', component: DraftSales },
  {
    id: 'stock:draft_transfers',
    kind: 'metric',
    permission: 'stock.transfer.view',
    component: DraftTransfers,
  },
  {
    id: 'receivables:outstanding',
    kind: 'metric',
    permission: 'receivables.receivable.view',
    component: OutstandingReceivables,
  },
  {
    id: 'cash_register:open_sessions',
    kind: 'metric',
    permission: 'cash_register.session.view',
    component: OpenCashSessions,
  },
  { id: 'sales:recent', kind: 'panel', permission: 'sales.sale.view', component: RecentSalesPanel },
];

export const DASHBOARD_SHORTCUTS: readonly DashboardShortcut[] = [
  {
    id: 'pos:open',
    labelKey: 'nav.pos',
    icon: 'pi pi-calculator',
    to: '/pos',
    permission: 'pos.terminal.use',
  },
  {
    id: 'sales:new',
    labelKey: 'sales.new',
    icon: 'pi pi-shopping-cart',
    to: '/sales/new',
    permission: 'sales.sale.create',
  },
  {
    id: 'stock:entry',
    labelKey: 'entries.new',
    icon: 'pi pi-download',
    to: '/stock/entries/new',
    permission: 'stock.entry.create',
  },
  {
    id: 'stock:exit',
    labelKey: 'exits.new',
    icon: 'pi pi-upload',
    to: '/stock/exits/new',
    permission: 'stock.exit.create',
  },
  {
    id: 'stock:transfer',
    labelKey: 'transfers.new',
    icon: 'pi pi-arrow-right-arrow-left',
    to: '/stock/transfers/new',
    permission: 'stock.transfer.create',
    feature: 'stock.transfers',
  },
];

/**
 * Éléments à afficher : ordre du profil UX (sans profil UX : ordre du registre), filtrés par
 * permission et fonctionnalité ; identifiants inconnus ignorés.
 */
export function selectDashboardItems<
  T extends { id: string; permission?: string; feature?: string },
>(
  registry: readonly T[],
  declared: readonly string[] | undefined,
  can: (permission: string) => boolean,
  features: readonly string[],
): T[] {
  const byId = new Map(registry.map((item) => [item.id, item]));
  const ids = declared ?? registry.map((item) => item.id);
  return ids
    .map((id) => byId.get(id))
    .filter((item): item is T => item !== undefined)
    .filter(
      (item) =>
        (item.permission === undefined || can(item.permission)) &&
        (item.feature === undefined || features.includes(item.feature)),
    );
}
