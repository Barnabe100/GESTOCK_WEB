import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatQuantity } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type StatusFilterValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { PageHeader } from '@/shared/ui/PageHeader';
import { RowActions } from '@/shared/ui/RowActions';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusFilter } from '@/shared/ui/StatusFilter';
import { ActiveBadge } from '@/shared/ui/StatusBadge';

import { RECIPE_MANAGE, recipeLabel, useRecipes, type RecipeSummary } from './api';

/**
 * Recettes (palier R8, ADR-0053) : fiches techniques des plats (articles non gérés en stock).
 * À la validation d'une vente, les ingrédients de la recette sortent du stock du site ; le
 * serveur fait foi pour toutes les règles.
 */
export default function RecipesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const canManage = can(RECIPE_MANAGE);
  const [table, setTable] = useState<TableState>({ ...INITIAL_TABLE, sortField: 'designation' });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const debounced = useDebouncedValue(search);
  const recipes = useRecipes(toQueryString(table, { search: debounced, status }));
  const filtered = search !== '' || status !== 'all';
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const open = (r: RecipeSummary) => void navigate(`/restaurant/recipes/${r.id}`);

  return (
    <>
      <PageHeader
        title={t('restaurantRecipes.title')}
        description={t('restaurantRecipes.subtitle')}
        actions={
          canManage && (
            <Button
              icon="pi pi-plus"
              label={t('restaurantRecipes.new')}
              onClick={() => void navigate('/restaurant/recipes/new')}
            />
          )
        }
      />
      <FilterBar
        onReset={() => {
          setSearch('');
          setStatus('all');
          resetPage();
        }}
        active={filtered}
      >
        <SearchInput
          value={search}
          placeholder={t('restaurantRecipes.search')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <StatusFilter
          value={status}
          onChange={(v) => {
            setStatus(v);
            resetPage();
          }}
        />
      </FilterBar>
      <ServerTable
        query={recipes}
        table={table}
        onTableChange={setTable}
        minWidth="40rem"
        onRowClick={open}
        empty={
          <ListEmpty filtered={filtered} title={t('restaurantRecipes.empty')} icon="pi pi-book" />
        }
      >
        <Column
          header={t('restaurantRecipes.dish')}
          sortField="designation"
          sortable
          body={(r: RecipeSummary) => (
            <span>
              <strong>{recipeLabel(r)}</strong>
              <br />
              <small className="sm-muted">{r.reference}</small>
            </span>
          )}
        />
        <Column
          header={t('restaurantRecipes.yield')}
          body={(r: RecipeSummary) =>
            r.yield_quantity
              ? t('restaurantRecipes.portions', {
                  count: Number(r.yield_quantity),
                  value: formatQuantity(r.yield_quantity, locale),
                })
              : '—'
          }
        />
        <Column
          header={t('restaurantRecipes.ingredients')}
          body={(r: RecipeSummary) => r.ingredient_count}
        />
        <Column
          header={t('restaurantRecipes.version')}
          body={(r: RecipeSummary) => (r.current_number ? `v${r.current_number}` : '—')}
        />
        <Column
          header={t('restaurantRecipes.updatedAt')}
          sortField="updated_at"
          sortable
          body={(r: RecipeSummary) => formatDateTime(r.updated_at, locale, timezone)}
        />
        <Column
          header={t('restaurantRecipes.status')}
          body={(r: RecipeSummary) => <ActiveBadge active={r.is_active} />}
        />
        <Column
          header={t('common.actions')}
          body={(r: RecipeSummary) => (
            <RowActions
              actions={[
                {
                  key: 'open',
                  label: t('actions.view'),
                  icon: 'pi pi-arrow-right',
                  onClick: () => open(r),
                },
              ]}
            />
          )}
        />
      </ServerTable>
    </>
  );
}
