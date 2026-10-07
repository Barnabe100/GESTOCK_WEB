import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { RowActions } from '@/shared/ui/RowActions';
import { confirmAction } from '@/shared/ui/confirm';
import { useToast } from '@/shared/ui/toast';

import {
  ASSORTMENT_MANAGE,
  useArticleSites,
  useAssortmentMutations,
  type Article,
  type ArticleSite,
  type RemovalBlocked,
} from './api';
import { AssortmentStateBadge, RemovalBlockers, removalBlocked } from './assortment';

/**
 * Fiche article — section « Sites » (Recette, étape 1, ADR-0046) : sur quels sites l'article
 * est proposé (assortiment), sur les sites visibles du membre. Aucun stock ici (catalogue ≠
 * assortiment ≠ stock) ; ajout, retrait et réactivation avec `catalog.assortment.manage`,
 * revérifiée par le serveur pour chaque site.
 */
export function ArticleAssortmentSection({ article }: { article: Article }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const canManage = can(ASSORTMENT_MANAGE);
  const sites = useArticleSites(article.id);
  const { add, remove } = useAssortmentMutations();
  const [blocked, setBlocked] = useState<RemovalBlocked[] | null>(null);

  const addTo = (site: ArticleSite) =>
    add.mutate(
      { siteId: site.site_id, articleIds: [article.id] },
      {
        onSuccess: () => toast.success(t('assortment.notice.added', { site: site.site_name })),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );

  const removeFrom = (site: ArticleSite) =>
    confirmAction(t, {
      header: t('assortment.removeTitle'),
      message: t('assortment.removeConfirmOne', {
        name: article.designation,
        site: site.site_name,
      }),
      acceptLabel: t('assortment.remove'),
      danger: true,
      onAccept: () =>
        remove.mutate(
          { siteId: site.site_id, articleIds: [article.id] },
          {
            onSuccess: () => toast.success(t('assortment.removed', { count: 1 })),
            onError: (error) => {
              const details = removalBlocked(error);
              if (details) setBlocked(details);
              else toast.error(translateError(t, error));
            },
          },
        ),
    });

  return (
    <section className="sm-block" aria-labelledby="article-assortment-title">
      <div className="sm-section-header">
        <h2 id="article-assortment-title">{t('assortment.sitesTitle')}</h2>
      </div>
      <p className="sm-help">{t('assortment.sitesHelp')}</p>
      {sites.isPending ? (
        <LoadingState />
      ) : sites.isError ? (
        <ErrorMessage error={sites.error} onRetry={() => void sites.refetch()} />
      ) : (
        <Card>
          <DataTable
            className="sm-table"
            value={Array.isArray(sites.data) ? sites.data : []}
            dataKey="site_id"
            tableStyle={{ minWidth: '28rem' }}
            emptyMessage={<EmptyState icon="pi pi-building" title={t('assortment.noSite')} />}
          >
            <Column field="site_name" header={t('layout.site')} />
            <Column
              header={t('assortment.stateLabel')}
              body={(s: ArticleSite) => <AssortmentStateBadge state={s.state} />}
            />
            <Column
              header={t('assortment.lastChange')}
              body={(s: ArticleSite) =>
                s.state === 'removed' && s.removed_at
                  ? formatDateTime(s.removed_at, locale, timezone)
                  : s.added_at
                    ? formatDateTime(s.added_at, locale, timezone)
                    : '—'
              }
            />
            {canManage && (
              <Column
                header={t('common.actions')}
                body={(s: ArticleSite) => (
                  <RowActions
                    actions={[
                      {
                        key: 'add',
                        label: t(
                          s.state === 'removed' ? 'assortment.reactivate' : 'assortment.addHere',
                        ),
                        icon: s.state === 'removed' ? 'pi pi-replay' : 'pi pi-plus',
                        onClick: () => addTo(s),
                        hidden: s.state === 'active' || !article.is_active,
                      },
                      {
                        key: 'remove',
                        label: t('assortment.remove'),
                        icon: 'pi pi-minus-circle',
                        danger: true,
                        onClick: () => removeFrom(s),
                        hidden: s.state !== 'active',
                      },
                    ]}
                  />
                )}
              />
            )}
          </DataTable>
        </Card>
      )}
      {blocked && (
        <Dialog
          header={t('assortment.removeTitle')}
          visible
          onHide={() => setBlocked(null)}
          className="sm-dialog"
        >
          <RemovalBlockers blocked={blocked} />
          <div className="sm-dialog-actions">
            <Button type="button" label={t('actions.close')} onClick={() => setBlocked(null)} />
          </div>
        </Dialog>
      )}
    </section>
  );
}
