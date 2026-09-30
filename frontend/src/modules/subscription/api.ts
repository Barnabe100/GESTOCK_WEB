import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { LimitUsage, SubscriptionStatus } from '@/core/api/types';
import type { Page } from '@/shared/lib/serverTable';
import type { LicenseState, SubscriptionPaymentStatus } from '@/shared/ui/StatusBadge';

/** Licence d'un site (lecture seule : l'entreprise ne la génère ni ne la modifie jamais). */
export interface LicenseSummary {
  id: string;
  license_number: string;
  license_version: number;
  state: LicenseState;
  plan_code: string;
  valid_from: string;
  valid_until: string;
  max_activations: number;
  /** Postes actifs du site et places restantes (calculés par le serveur). */
  activations_used: number;
  activations_available: number;
  issued_at: string;
  revoked_at: string | null;
}

/**
 * Poste d'un site : installation cliente activée sous la licence (le Web n'active jamais de
 * navigateur). Libérer un poste libère une place, rien d'autre.
 */
export interface LicenseActivation {
  id: string;
  site_id: string;
  subscription_id: string;
  license_id: string;
  installation_id: string;
  label: string;
  client_version: string | null;
  status: 'ACTIVE' | 'RELEASED';
  activated_at: string;
  last_seen_at: string;
  /** Non vu depuis plus que la durée hors ligne tolérée. */
  stale: boolean;
  released_at: string | null;
  release_source: 'TENANT' | 'TECHNOVA' | null;
  release_reason: string | null;
}

/** Abonnement d'un site (1 site = 1 abonnement, ADR-0033) ; ``site`` nul : abonnement pris à
 * l'inscription, rattaché au premier site créé. */
export interface SubscriptionDetails {
  id: string;
  site: { id: string; name: string; code: string } | null;
  plan_code: string;
  plan_name: string;
  billing_period: 'monthly' | 'annual';
  status: SubscriptionStatus;
  effective_status: SubscriptionStatus;
  started_at: string;
  current_period_start: string;
  current_period_end: string;
  grace_days: number;
  requested_activations: number;
  limits: Record<string, LimitUsage>;
  features: string[];
  allowed_access: string[];
  /** Licence en vigueur du site, sinon la plus récente ; nulle : aucune licence. */
  license: LicenseSummary | null;
  /** Offre dont les droits sont en vigueur (celle de la licence en vigueur, R4). */
  effective_plan: PlanRef;
  /** Offre de la prochaine licence si elle diffère (changement de plan non encore effectif). */
  next_plan: PlanRef | null;
  /** Prochaine période du site, calculée par le serveur. */
  renewal: RenewalQuote;
}

export interface PlanRef {
  code: string;
  name: string;
}

/**
 * Devis de la prochaine période d'un site (Phase 3.3-B4) : période, postes et montant sont
 * **calculés par le serveur** ; l'interface les affiche seulement. ``amount`` nul : offre sans
 * tarif (montant convenu avec TechNova, saisi à la déclaration).
 */
export interface RenewalQuote {
  subscription_id: string;
  site_id: string | null;
  kind: 'initial' | 'renewal';
  plan: PlanRef;
  billing_period: 'monthly' | 'annual';
  valid_from: string;
  valid_until: string;
  activations: number;
  current_activations: number | null;
  activations_explicit: boolean;
  amount: string | null;
  currency: string | null;
  coverage_end: string | null;
  /** La période commence avant aujourd'hui : renouvellement pendant la grâce, sans perte. */
  grace_continuity: boolean;
  /** Action « Renouveler » proposée. */
  renewal_due: boolean;
}

/** Devis pour un autre nombre de postes (demande explicite ; TechNova confirme). */
export function useRenewalQuote(subscriptionId: string, requestedActivations: number | null) {
  return useQuery({
    queryKey: ['subscription', 'quote', subscriptionId, requestedActivations],
    queryFn: ({ signal }) =>
      api.get<RenewalQuote>(
        `/subscriptions/${encodeURIComponent(subscriptionId)}/renewal-quote` +
          (requestedActivations ? `?requested_activations=${requestedActivations}` : ''),
        signal,
      ),
    enabled: Boolean(subscriptionId),
    placeholderData: (previous) => previous,
  });
}

/** Abonnements de l'entreprise (sites accessibles au membre). */
export function useSubscriptions(enabled = true) {
  return useQuery({
    queryKey: ['subscription', 'list'],
    queryFn: ({ signal }) => api.get<SubscriptionDetails[]>('/subscriptions', signal),
    enabled,
  });
}

// --- Paiements de l'abonnement à TechNova (Phase 3.3-A, ADR-0032) ------------------------------

export const SUBSCRIPTION_PAYMENT_METHODS = [
  'BANK_TRANSFER',
  'MOBILE_MONEY',
  'CASH',
  'CHECK',
  'CARD',
  'OTHER',
] as const;
export type SubscriptionPaymentMethod = (typeof SUBSCRIPTION_PAYMENT_METHODS)[number];

/** Vue de l'entreprise : jamais l'identité de l'agent TechNova qui a décidé. */
export interface SubscriptionPayment {
  id: string;
  subscription_id: string;
  amount: string;
  currency: string;
  period_start: string;
  period_end: string;
  payment_method: SubscriptionPaymentMethod;
  declared_reference: string;
  /** Nombre de postes demandé explicitement pour cette période (nul : reconduction). */
  requested_activations: number | null;
  declared_by: string;
  status: SubscriptionPaymentStatus;
  created_at: string;
  decided_at: string | null;
  rejection_reason: string | null;
}

/**
 * Déclaration : ni période, ni devise, ni champ de décision (calculés par le serveur ou fixés
 * par TechNova ; le serveur refuse tout champ inconnu). ``amount`` seulement pour une offre sans
 * tarif ; ``requested_activations`` seulement pour demander un autre nombre de postes.
 */
export interface SubscriptionPaymentInput {
  subscription_id: string;
  amount?: string;
  requested_activations?: number;
  payment_method: SubscriptionPaymentMethod;
  declared_reference: string;
  idempotency_key: string;
}

const PAYMENTS_KEY = ['subscription', 'payments'] as const;

export function useSubscriptionPayments(query: string) {
  return useQuery({
    queryKey: [...PAYMENTS_KEY, query],
    queryFn: ({ signal }) =>
      api.get<Page<SubscriptionPayment>>(`/subscription/payments?${query}`, signal),
    placeholderData: (previous) => previous,
  });
}

export function useDeclareSubscriptionPayment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: SubscriptionPaymentInput) =>
      api.post<SubscriptionPayment>('/subscription/payments', input),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: PAYMENTS_KEY }),
  });
}

// --- Rappels d'échéance (Phase 3.3-B4, ADR-0036) ------------------------------------------------

/** Rappel d'échéance d'un site : lu / non lu propre au membre ; historique conservé. */
export interface AppNotification {
  id: string;
  kind: 'subscription.expiry';
  /** Jours avant l'échéance (négatif : après). */
  step: number;
  /** Dernier jour couvert (jour du fuseau de l'entreprise). */
  reference_date: string;
  site: { id: string; name: string; code: string } | null;
  subscription_id: string | null;
  data: { days_left?: number; plan_code?: string; effective_status?: string; trial?: boolean };
  created_at: string;
  read_at: string | null;
}

const NOTIFICATIONS_KEY = ['notifications'] as const;

export function useNotifications(query: string, enabled = true) {
  return useQuery({
    queryKey: [...NOTIFICATIONS_KEY, 'list', query],
    queryFn: ({ signal }) => api.get<Page<AppNotification>>(`/notifications?${query}`, signal),
    placeholderData: (previous) => previous,
    enabled,
  });
}

export function useUnreadNotifications(enabled: boolean) {
  return useQuery({
    queryKey: [...NOTIFICATIONS_KEY, 'unread'],
    queryFn: ({ signal }) => api.get<{ unread: number }>('/notifications/unread-count', signal),
    enabled,
    refetchInterval: 5 * 60_000,
  });
}

/** Marque une notification (ou toutes, ``id`` nul) comme lue par le membre. */
export function useMarkNotificationsRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string | null) =>
      api.post<void>(
        id ? `/notifications/${encodeURIComponent(id)}/read` : '/notifications/read-all',
        {},
      ),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_KEY }),
  });
}

// --- Postes (Phase 3.3-B3) ----------------------------------------------------------------------

const ACTIVATIONS_KEY = ['subscription', 'activations'] as const;

/** Postes actifs d'un site. */
export function useSiteActivations(siteId: string | undefined) {
  return useQuery({
    queryKey: [...ACTIVATIONS_KEY, siteId],
    queryFn: ({ signal }) =>
      api.get<Page<LicenseActivation>>(
        `/license-activations?site_id=${encodeURIComponent(siteId ?? '')}&status=ACTIVE&limit=100`,
        signal,
      ),
    enabled: Boolean(siteId),
  });
}

/** Libère un poste (raison obligatoire) ; le serveur revérifie droits, site et état. */
export function useReleaseActivation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.post<LicenseActivation>(`/license-activations/${encodeURIComponent(id)}/release`, {
        reason,
      }),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ACTIVATIONS_KEY });
      void queryClient.invalidateQueries({ queryKey: ['subscription', 'list'] });
    },
  });
}
