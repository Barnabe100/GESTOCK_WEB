import { Button } from 'primereact/button';
import { InputText } from 'primereact/inputtext';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * Zone de recherche et de filtres, identique sur toutes les listes :
 * [ Recherche ] [ Filtres… ] [ Réinitialiser ]. Le bouton n'est actif que si un filtre
 * s'écarte de sa valeur par défaut.
 */
export function FilterBar({
  children,
  onReset,
  active = false,
}: {
  children: ReactNode;
  onReset?: () => void;
  active?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="sm-filterbar" role="search">
      {children}
      {onReset && (
        <Button
          type="button"
          icon="pi pi-filter-slash"
          label={t('filters.reset')}
          text
          severity="secondary"
          disabled={!active}
          onClick={onReset}
        />
      )}
    </div>
  );
}

/** Période (du … au …), libellés visibles. Valeurs ISO `AAAA-MM-JJ` (vide = sans borne). */
export function DateRangeFilter({
  from,
  to,
  onChange,
}: {
  from: string;
  to: string;
  onChange: (range: { from: string; to: string }) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="sm-daterange" role="group" aria-label={t('filters.period')}>
      <label className="sm-daterange-item">
        <span>{t('stock.dateFrom')}</span>
        <InputText
          type="date"
          value={from}
          aria-label={t('stock.dateFrom')}
          onChange={(e) => onChange({ from: e.target.value, to })}
        />
      </label>
      <label className="sm-daterange-item">
        <span>{t('stock.dateTo')}</span>
        <InputText
          type="date"
          value={to}
          aria-label={t('stock.dateTo')}
          onChange={(e) => onChange({ from, to: e.target.value })}
        />
      </label>
    </div>
  );
}
