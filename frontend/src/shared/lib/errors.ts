import type { TFunction } from 'i18next';

import { ApiError } from '@/core/api/client';

/** Message utilisateur traduit à partir du `code` d'erreur renvoyé par l'API. */
export function translateError(t: TFunction, error: unknown): string {
  if (error instanceof ApiError) {
    return t(`errors:${error.code}`, {
      ...error.extra,
      defaultValue: error.detail || t('errors:unknown'),
    });
  }
  return t('errors:unknown');
}

/**
 * Champs refusés par la validation du serveur (`validation_error`, `loc` = ["body", champ]) :
 * le serveur reste la source de validation, l'interface signale seulement le champ concerné.
 */
export function invalidFields(error: unknown): string[] {
  if (!(error instanceof ApiError) || error.code !== 'validation_error') return [];
  const errors = (error.extra.errors ?? []) as { loc?: unknown[] }[];
  return errors
    .map((e) => (e.loc && e.loc[0] === 'body' ? e.loc[1] : undefined))
    .filter((field): field is string => typeof field === 'string');
}
