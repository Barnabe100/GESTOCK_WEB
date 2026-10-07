import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { OutOfAssortmentBadge } from '@/modules/catalog/assortment';
import { formatQuantity } from '@/shared/lib/decimal';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { RowActions } from '@/shared/ui/RowActions';

import { LOCATION_MANAGE, useStockLevels, type StockLevel } from './api';
import { LocationAssignDialog, LocationLabel } from './LocationAssignDialog';
import { LevelStateTag } from './ui';

/**
 * Fiche article — vue PAR SITE (Lot 3-F) : stock et emplacement courant de l'article sur
 * chaque site visible du membre (le serveur limite les sites). Un emplacement différent par
 * site ; affectation avec `stock.location.manage`. Chargée seulement avec `stock.level.view`.
 */
export default function ArticleSitesPanel({
  articleId,
  reference,
}: {
  articleId: string;
  reference: string;
}) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const canLocate = can(LOCATION_MANAGE);
  const [locating, setLocating] = useState<StockLevel | null>(null);
  const levels = useStockLevels(
    new URLSearchParams({
      article_id: articleId,
      include_inactive: 'true',
      sort: 'site',
      limit: '100',
    }).toString(),
  );

  return (
    <section className="sm-block" aria-labelledby="article-sites-title">
      <div className="sm-section-header">
        <h2 id="article-sites-title">{t('locations.perSite')}</h2>
      </div>
      <p className="sm-help">{t('locations.perSiteHelp')}</p>
      {levels.isPending ? (
        <LoadingState />
      ) : levels.isError ? (
        <ErrorMessage error={levels.error} onRetry={() => void levels.refetch()} />
      ) : (
        <Card>
          <DataTable
            className="sm-table"
            value={levels.data.items}
            dataKey="site_id"
            tableStyle={{ minWidth: '32rem' }}
            emptyMessage={<EmptyState icon="pi pi-warehouse" title={t('locations.noSite')} />}
          >
            <Column field="site_name" header={t('layout.site')} />
            <Column
              header={t('stock.quantity')}
              headerClassName="sm-num"
              bodyClassName="sm-num"
              body={(l: StockLevel) => `${formatQuantity(l.quantity, locale)} ${l.unit}`}
            />
            <Column
              header={t('stock.state')}
              body={(l: StockLevel) => (
                <div className="sm-tags">
                  <LevelStateTag state={l.state} />
                  {l.in_assortment === false && <OutOfAssortmentBadge />}
                </div>
              )}
            />
            <Column
              header={t('locations.location')}
              body={(l: StockLevel) => (
                <LocationLabel name={l.location_name} active={l.location_active} />
              )}
            />
            {canLocate && (
              <Column
                header={t('common.actions')}
                body={(l: StockLevel) => (
                  <RowActions
                    actions={[
                      {
                        key: 'location',
                        label: t('locations.assign'),
                        icon: 'pi pi-map-marker',
                        onClick: () => setLocating(l),
                        hidden: l.in_assortment === false && !l.location_id,
                      },
                    ]}
                  />
                )}
              />
            )}
          </DataTable>
        </Card>
      )}
      {locating && (
        <LocationAssignDialog
          siteId={locating.site_id}
          siteName={locating.site_name}
          articleId={articleId}
          articleLabel={reference}
          currentId={locating.location_id ?? null}
          currentName={locating.location_name}
          onClose={() => setLocating(null)}
        />
      )}
    </section>
  );
}
