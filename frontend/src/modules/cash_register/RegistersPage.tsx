import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type StatusFilterValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { confirmAction } from '@/shared/ui/confirm';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { PageHeader } from '@/shared/ui/PageHeader';
import { RowActions } from '@/shared/ui/RowActions';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { StatusFilter } from '@/shared/ui/StatusFilter';
import { useToast } from '@/shared/ui/toast';

import { useCashMutations, useCashRegisters, type CashRegister } from './api';
import { OpenSessionDialog } from './OpenSessionDialog';
import { RegisterDialog } from './RegisterDialog';

/**
 * Caisses des sites visibles : statut (ouverte / fermée / désactivée), caissier de la session
 * en cours et solde théorique calculé par le serveur ; ouverture, session, clôture, gestion.
 */
export default function RegistersPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const multiSite = capabilities.site === null && capabilities.sites.length > 1;
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const [siteId, setSiteId] = useState<string | null>(null);
  const [editing, setEditing] = useState<CashRegister | 'new' | null>(null);
  const [opening, setOpening] = useState<CashRegister | null>(null);
  const debounced = useDebouncedValue(search);
  const registers = useCashRegisters(
    toQueryString(table, { search: debounced, status, site_id: siteId }),
  );
  const { setActive } = useCashMutations();
  const canManage = can('cash_register.register.manage');
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered = search !== '' || status !== 'all' || siteId !== null;

  const toggle = (r: CashRegister) => {
    const run = () =>
      setActive.mutate(
        { id: r.id, active: !r.is_active },
        {
          onSuccess: () =>
            toast.success(t(r.is_active ? 'cash.registerDeactivated' : 'cash.registerActivated')),
          onError: (e) => toast.error(translateError(t, e)),
        },
      );
    if (!r.is_active) {
      run();
      return;
    }
    confirmAction(t, {
      header: t('cash.deactivateTitle'),
      message: t('cash.deactivateConfirm', { name: r.name }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: run,
    });
  };

  return (
    <>
      <PageHeader
        title={t('cash.registersTitle')}
        description={t('cash.registersSubtitle')}
        actions={
          canManage && (
            <Button
              icon="pi pi-plus"
              label={t('cash.newRegister')}
              onClick={() => setEditing('new')}
            />
          )
        }
      />
      <FilterBar
        onReset={() => {
          setSearch('');
          setStatus('all');
          setSiteId(null);
          resetPage();
        }}
        active={filtered}
      >
        <SearchInput
          value={search}
          placeholder={t('cash.registerSearch')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        {multiSite && (
          <Dropdown
            value={siteId}
            onChange={(e) => {
              setSiteId((e.value as string | undefined) ?? null);
              resetPage();
            }}
            options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
            placeholder={t('sales.allSites')}
            showClear
            aria-label={t('layout.site')}
          />
        )}
        <StatusFilter
          value={status}
          onChange={(v) => {
            setStatus(v);
            resetPage();
          }}
        />
      </FilterBar>
      <ServerTable
        query={registers}
        table={table}
        onTableChange={setTable}
        minWidth="56rem"
        empty={
          <ListEmpty
            filtered={filtered}
            icon="pi pi-box"
            title={t('cash.registersEmpty')}
            action={
              canManage && (
                <Button
                  icon="pi pi-plus"
                  label={t('cash.newRegister')}
                  outlined
                  onClick={() => setEditing('new')}
                />
              )
            }
          />
        }
      >
        <Column field="code" header={t('cash.code')} sortable bodyClassName="sm-nowrap" />
        <Column field="name" header={t('cash.registerName')} sortable />
        {multiSite && <Column field="site_name" header={t('layout.site')} />}
        <Column
          header={t('cash.status')}
          body={(r: CashRegister) =>
            !r.is_active ? (
              <StatusBadge tone="neutral" label={t('common.inactive')} />
            ) : r.current_session ? (
              <StatusBadge tone="success" label={t('cash.sessionStatus.OPEN')} />
            ) : (
              <StatusBadge tone="neutral" label={t('cash.sessionStatus.CLOSED')} />
            )
          }
        />
        <Column
          header={t('cash.cashier')}
          body={(r: CashRegister) =>
            r.current_session ? (
              <div>
                <div>{r.current_session.opened_by_name}</div>
                <div className="sm-help">
                  {formatDateTime(r.current_session.opened_at, locale, timezone)}
                </div>
              </div>
            ) : (
              '—'
            )
          }
        />
        <Column
          header={t('cash.theoreticalBalance')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(r: CashRegister) =>
            r.current_session
              ? formatMoney(r.current_session.theoretical_balance, currency, locale)
              : '—'
          }
        />
        <Column
          header={t('common.actions')}
          body={(r: CashRegister) => (
            <RowActions
              actions={[
                {
                  key: 'open',
                  label: t('cash.open'),
                  icon: 'pi pi-lock-open',
                  hidden:
                    !r.is_active ||
                    r.current_session !== null ||
                    !can('cash_register.session.open'),
                  onClick: () => setOpening(r),
                },
                {
                  key: 'session',
                  label: t('cash.viewSession'),
                  icon: 'pi pi-eye',
                  hidden: r.current_session === null || !can('cash_register.session.view'),
                  onClick: () => void navigate(`/cash/sessions/${r.current_session?.id ?? ''}`),
                },
                {
                  key: 'close',
                  label: t('cash.close'),
                  icon: 'pi pi-lock',
                  hidden: r.current_session === null || !can('cash_register.session.close'),
                  onClick: () =>
                    void navigate(`/cash/sessions/${r.current_session?.id ?? ''}?close=1`),
                },
                {
                  key: 'edit',
                  label: t('actions.edit'),
                  icon: 'pi pi-pencil',
                  hidden: !canManage,
                  onClick: () => setEditing(r),
                },
                {
                  key: 'toggle',
                  label: t(r.is_active ? 'actions.deactivate' : 'actions.activate'),
                  icon: r.is_active ? 'pi pi-ban' : 'pi pi-check',
                  danger: r.is_active,
                  hidden: !canManage,
                  onClick: () => toggle(r),
                },
              ]}
            />
          )}
        />
      </ServerTable>
      {editing && (
        <RegisterDialog
          register={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
      {opening && <OpenSessionDialog register={opening} onClose={() => setOpening(null)} />}
    </>
  );
}
