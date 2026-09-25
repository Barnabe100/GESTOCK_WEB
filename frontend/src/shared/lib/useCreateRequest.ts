import { useCallback } from 'react';
import { useSearchParams } from 'react-router';

/**
 * Ouverture directe du formulaire de création depuis un lien (`?create=1`, ex. action d'une
 * étape d'onboarding), seulement si l'utilisateur peut créer (ergonomie : le backend applique
 * sa permission). Renvoie la demande et de quoi retirer le paramètre à la fermeture.
 */
export function useCreateRequest(allowed: boolean): [boolean, () => void] {
  const [params, setParams] = useSearchParams();
  const requested = allowed && params.get('create') === '1';
  const clear = useCallback(() => {
    if (!params.has('create')) return;
    const next = new URLSearchParams(params);
    next.delete('create');
    setParams(next, { replace: true });
  }, [params, setParams]);
  return [requested, clear];
}
