import { Dialog } from 'primereact/dialog';
import { Message } from 'primereact/message';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { ApiError } from '@/core/api/client';
import { formatQuantity } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { LoadingState } from '@/shared/ui/LoadingState';

import { RECIPE_VIEW, RECIPES_MODULE, useRecipeSheet } from './api';

export interface SheetTarget {
  articleId: string;
  packagingId: string | null;
  quantity: string;
  label: string;
}

/** La fiche technique est proposée : module effectif ET consultation des recettes (le serveur
 *  revérifie). */
export function useCanSeeSheets(): boolean {
  const { can, hasModule } = useCapabilities();
  return hasModule(RECIPES_MODULE) && can(RECIPE_VIEW);
}

/**
 * Fiche technique d'une ligne (écran cuisine, ADR-0053 D13) : ingrédients mis à l'échelle de la
 * quantité commandée, instructions ; aucun coût. Calculée par le serveur (même règle que la
 * déduction à la vente).
 */
export function RecipeSheetDialog({
  target,
  onClose,
}: {
  target: SheetTarget;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { locale } = capabilities.tenant;
  const sheet = useRecipeSheet(target);
  return (
    <Dialog
      header={t('restaurantRecipes.sheetOf', { name: target.label })}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      {sheet.isPending ? (
        <LoadingState />
      ) : sheet.isError ? (
        <Message
          severity="info"
          text={
            sheet.error instanceof ApiError && sheet.error.code === 'recipe_not_found'
              ? t('restaurantRecipes.sheetUnavailable')
              : translateError(t, sheet.error)
          }
        />
      ) : (
        <div className="sm-form">
          <p className="sm-help">
            {t('restaurantRecipes.sheetFor', {
              quantity: `${formatQuantity(sheet.data.quantity, locale)}${
                sheet.data.packaging_name ? ` ${sheet.data.packaging_name}` : ''
              }`,
              portions: formatQuantity(sheet.data.portions, locale),
              number: sheet.data.version_number,
            })}
          </p>
          <ul className="sm-recipe-sheet" aria-label={t('restaurantRecipes.ingredients')}>
            {sheet.data.lines.map((line) => (
              <li key={line.reference}>
                <strong>
                  {formatQuantity(line.quantity, locale)} {line.unit}
                </strong>{' '}
                {line.designation}
                {line.note && <small className="sm-help"> — {line.note}</small>}
              </li>
            ))}
          </ul>
          {sheet.data.instructions && (
            <>
              <h3>{t('restaurantRecipes.instructions')}</h3>
              <p className="sm-preline">{sheet.data.instructions}</p>
            </>
          )}
        </div>
      )}
    </Dialog>
  );
}
