import { Button } from 'primereact/button';
import { Menu } from 'primereact/menu';
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

export interface RowAction {
  key: string;
  label: string;
  icon: string;
  onClick: () => void;
  /** Action destructive (désactivation, annulation…) : rouge, jamais confondue. */
  danger?: boolean;
  /** Non autorisée ici (permission, statut) : l'action n'est pas proposée. */
  hidden?: boolean;
}

/**
 * Actions d'une ligne de tableau : au plus `inline` boutons-icônes (libellé en infobulle et
 * pour les lecteurs d'écran), les suivantes regroupées dans un menu « Plus d'actions ».
 */
export function RowActions({ actions, inline = 3 }: { actions: RowAction[]; inline?: number }) {
  const { t } = useTranslation();
  const menu = useRef<Menu>(null);
  const visible = actions.filter((a) => !a.hidden);
  const direct = visible.length > inline ? visible.slice(0, inline - 1) : visible;
  const more = visible.slice(direct.length);
  return (
    <div className="sm-row-actions">
      {direct.map((a) => (
        <Button
          key={a.key}
          type="button"
          icon={a.icon}
          text
          rounded
          severity={a.danger ? 'danger' : undefined}
          aria-label={a.label}
          tooltip={a.label}
          tooltipOptions={{ position: 'top', showDelay: 300 }}
          onClick={a.onClick}
        />
      ))}
      {more.length > 0 && (
        <>
          <Menu
            ref={menu}
            popup
            model={more.map((a) => ({
              label: a.label,
              icon: a.icon,
              className: a.danger ? 'sm-menu-danger' : undefined,
              command: a.onClick,
            }))}
          />
          <Button
            type="button"
            icon="pi pi-ellipsis-v"
            text
            rounded
            aria-label={t('actions.more')}
            aria-haspopup
            onClick={(e) => menu.current?.toggle(e)}
          />
        </>
      )}
    </div>
  );
}
