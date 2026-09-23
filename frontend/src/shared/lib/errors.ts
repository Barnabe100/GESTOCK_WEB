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
