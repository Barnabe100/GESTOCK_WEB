import { Button } from 'primereact/button';
import { Message } from 'primereact/message';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  ASSORTMENT_MANAGE,
  useArticleSites,
  useAssortmentMutations,
  type AssortmentState,
  type RemovalBlocked,
} from './api';

/**
 * Assortiment par site (Recette, étape 1, ADR-0046) — composants partagés par la page
 * Assortiment, la fiche article et les écrans opérationnels. CATALOGUE ≠ ASSORTIMENT ≠ STOCK :
 * le serveur décide toujours (permission par site, abonnement du site, blocages du retrait) ;
 * l'interface guide seulement.
 */

const STATE_TONES: Record<AssortmentState, Tone> = {
  active: 'success',
  removed: 'warning',
  none: 'neutral',
};

export function AssortmentStateBadge({ state }: { state: AssortmentState }) {
  const { t } = useTranslation();
  return <StatusBadge tone={STATE_TONES[state]} label={t(`assortment.state.${state}`)} />;
}

/** Badge « Hors assortiment » : stock restant d'un article retiré, ou article non proposé. */
export function OutOfAssortmentBadge() {
  const { t } = useTranslation();
  return <StatusBadge tone="warning" icon="pi pi-ban" label={t('assortment.outOfAssortment')} />;
}

/** Détail d'un retrait refusé (`409 article_has_stock` / `article_in_open_documents`), ou `null`. */
export function removalBlocked(error: unknown): RemovalBlocked[] | null {
  if (!(error instanceof ApiError)) return null;
  if (error.code !== 'article_has_stock' && error.code !== 'article_in_open_documents') return null;
  const blocked = error.extra.blocked;
  return Array.isArray(blocked) ? (blocked as RemovalBlocked[]) : null;
}

/**
 * Raison du refus, article par article, à partir du détail renvoyé par le serveur : les
 * messages génériques `article_has_stock` / `article_in_open_documents` (changement de suivi,
 * article non géré) ne décrivent pas un retrait de l'assortiment.
 */
export function RemovalBlockers({ blocked }: { blocked: RemovalBlocked[] }) {
  const { t } = useTranslation();
  return (
    <div className="sm-removal-blockers" role="alert">
      <p className="sm-strong">{t('assortment.removal.refused', { count: blocked.length })}</p>
      <ul>
        {blocked.map((b) => (
          <li key={b.reference}>
            <span className="sm-code">{b.reference}</span> —{' '}
            {b.reason === 'stock'
              ? t('assortment.removal.stock')
              : t('assortment.removal.documents', {
                  documents: b.documents.join(', '),
                  count: b.documents.length,
                })}
          </li>
        ))}
      </ul>
      <p className="sm-help">{t('assortment.removal.help')}</p>
    </div>
  );
}

/**
 * Écrans opérationnels (documents de stock, ventes, transferts) : l'article choisi est-il
 * proposé par le(s) site(s) de l'opération ? Sinon, le serveur refusera l'enregistrement
 * (`422 article_not_in_site_assortment`, jamais d'ajout automatique) : l'interface le signale
 * et propose l'ajout explicite à qui en a la permission (contrôlée par le serveur, auditée).
 */
export function AssortmentNotice({
  articleId,
  siteIds,
}: {
  articleId: string;
  siteIds: (string | null | undefined)[];
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const wanted = siteIds.filter((s): s is string => Boolean(s));
  const sites = useArticleSites(articleId, wanted.length > 0);
  const { add } = useAssortmentMutations();
  // Seuls les sites visibles du membre sont décrits ; pour les autres, le serveur tranche.
  const missing = (Array.isArray(sites.data) ? sites.data : []).filter(
    (s) => wanted.includes(s.site_id) && s.state !== 'active',
  );
  if (missing.length === 0) return null;
  const canManage = can(ASSORTMENT_MANAGE);
  return (
    <div className="sm-line-notice">
      {missing.map((site) => (
        <Message
          key={site.site_id}
          severity="warn"
          content={
            <div className="sm-assortment-notice">
              <span>{t('assortment.notice.notOffered', { site: site.site_name })}</span>
              {canManage ? (
                <Button
                  type="button"
                  size="small"
                  outlined
                  icon="pi pi-plus"
                  label={t('assortment.notice.add')}
                  loading={add.isPending}
                  onClick={() =>
                    add.mutate(
                      { siteId: site.site_id, articleIds: [articleId] },
                      {
                        onSuccess: () =>
                          toast.success(t('assortment.notice.added', { site: site.site_name })),
                        onError: (error) => toast.error(translateError(t, error)),
                      },
                    )
                  }
                />
              ) : (
                <span className="sm-help">{t('assortment.notice.askManager')}</span>
              )}
            </div>
          }
        />
      ))}
    </div>
  );
}
