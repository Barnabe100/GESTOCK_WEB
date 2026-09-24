import type { UseQueryResult } from '@tanstack/react-query';
import { DataTable, type DataTableStateEvent } from 'primereact/datatable';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type { Page, TableState } from '@/shared/lib/serverTable';

import { EmptyState } from './EmptyState';
import { ErrorMessage } from './ErrorMessage';

/**
 * Tableau de liste standard (pagination, tri et filtres côté serveur) : même densité,
 * pagination, état de chargement, état vide et gestion d'erreur sur toutes les listes.
 * `minWidth` : largeur sous laquelle le tableau défile horizontalement (petits écrans) au
 * lieu d'écraser ses colonnes.
 */
export function ServerTable<T extends object>({
  query,
  table,
  onTableChange,
  empty,
  minWidth = '48rem',
  dataKey = 'id',
  rowClassName,
  onRowClick,
  children,
}: {
  query: UseQueryResult<Page<T>>;
  table: TableState;
  onTableChange: (state: TableState) => void;
  empty?: ReactNode;
  minWidth?: string;
  dataKey?: string | ((row: T) => string);
  rowClassName?: (row: T) => string | undefined;
  /** Ouverture au clic sur la ligne (confort) ; une action « Ouvrir » reste accessible au clavier. */
  onRowClick?: (row: T) => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  if (query.isError) {
    return <ErrorMessage error={query.error} onRetry={() => void query.refetch()} />;
  }
  const onChange = (e: DataTableStateEvent) =>
    onTableChange({ first: e.first, rows: e.rows, sortField: e.sortField, sortOrder: e.sortOrder });
  return (
    <DataTable
      className="sm-table"
      tableStyle={{ minWidth }}
      value={query.data?.items ?? []}
      loading={query.isFetching}
      dataKey={dataKey}
      lazy
      paginator
      first={table.first}
      rows={table.rows}
      rowsPerPageOptions={[10, 25, 50, 100]}
      totalRecords={query.data?.total ?? 0}
      sortField={table.sortField}
      sortOrder={table.sortOrder}
      onPage={onChange}
      onSort={onChange}
      rowHover
      rowClassName={(row: T) =>
        [onRowClick ? 'sm-clickable' : '', rowClassName?.(row) ?? ''].join(' ').trim()
      }
      onRowClick={onRowClick ? (e) => onRowClick(e.data as T) : undefined}
      paginatorTemplate="RowsPerPageDropdown CurrentPageReport PrevPageLink PageLinks NextPageLink"
      currentPageReportTemplate={t('table.pageReport')}
      emptyMessage={empty ?? <EmptyState />}
    >
      {children}
    </DataTable>
  );
}
