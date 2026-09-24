import type { TFunction } from 'i18next';
import { confirmDialog } from 'primereact/confirmdialog';

/**
 * Confirmation standard d'une action sensible (validation, annulation, désactivation…).
 * Le dialogue est monté une fois dans la coquille de l'application (`<ConfirmDialog />`).
 */
export function confirmAction(
  t: TFunction,
  {
    header,
    message,
    acceptLabel,
    danger = false,
    onAccept,
  }: {
    header: string;
    message: string;
    acceptLabel: string;
    danger?: boolean;
    onAccept: () => void | Promise<void>;
  },
) {
  confirmDialog({
    header,
    message,
    icon: danger ? 'pi pi-exclamation-triangle' : 'pi pi-question-circle',
    acceptLabel,
    rejectLabel: t('actions.cancel'),
    acceptClassName: danger ? 'p-button-danger' : undefined,
    rejectClassName: 'p-button-text p-button-secondary',
    defaultFocus: 'reject',
    className: 'sm-dialog',
    accept: () => void onAccept(),
  });
}
