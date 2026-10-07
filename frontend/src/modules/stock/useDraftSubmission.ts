import { useRef, useState } from 'react';

/**
 * Enregistrement puis validation d'un brouillon de document de stock (entrée, sortie,
 * transfert), sans double création ni perte de modification.
 *
 * - L'identifiant du brouillon est conservé dès sa création (référence, indépendante du rendu
 *   et de la navigation) : tant que le formulaire « nouveau » reste affiché après la création,
 *   un nouvel enregistrement met à jour CE brouillon au lieu d'en créer un second.
 * - Une modification est détectée en comparant la saisie, sous sa forme envoyée au serveur, à
 *   la dernière version enregistrée — jamais par l'indicateur `isDirty` de react-hook-form, qui
 *   n'est pas calculé tant qu'il n'a pas été lu au rendu.
 * - Une seule soumission à la fois (verrou synchrone : double clic, clics rapprochés).
 */
export function useDraftSubmission<Values, Input, Saved extends { id: string }>({
  initial,
  valuesOf,
  toInput,
  save,
  onSaved,
}: {
  /** Brouillon déjà enregistré (`undefined` pour un nouveau document). */
  initial: Saved | undefined;
  /** Valeurs du formulaire d'un brouillon enregistré (celles du `reset` après enregistrement). */
  valuesOf: (saved: Saved) => Values;
  /** Corps envoyé au serveur ; `create` : première création du brouillon. */
  toInput: (values: Values, create: boolean) => Input;
  save: (id: string | undefined, input: Input) => Promise<Saved>;
  /** Après chaque enregistrement ; `created` : le brouillon vient d'être créé. */
  onSaved: (saved: Saved, created: boolean) => void;
}) {
  // Forme comparable de la saisie (forme « mise à jour », sans les champs de création), relue
  // sur les valeurs du serveur après enregistrement (« 40 » envoyé, « 40.000 » relu).
  const signature = (values: Values) => JSON.stringify(toInput(values, false));
  const draft = useRef<Saved | undefined>(initial);
  const savedSignature = useRef<string | null | undefined>(undefined);
  if (savedSignature.current === undefined) {
    savedSignature.current = initial ? signature(valuesOf(initial)) : null;
  }
  const locked = useRef(false);
  const [pending, setPending] = useState(false);

  /** Enregistre la saisie : crée le brouillon la première fois, met à jour ensuite. */
  const persist = async (values: Values): Promise<Saved> => {
    const current = draft.current;
    const saved = await save(current?.id, toInput(values, current === undefined));
    draft.current = saved;
    savedSignature.current = signature(valuesOf(saved));
    onSaved(saved, current === undefined);
    return saved;
  };

  /** Brouillon à jour de la saisie : enregistré seulement s'il manque ou diffère. */
  const ensureSaved = async (values: Values): Promise<Saved> => {
    const current = draft.current;
    if (current !== undefined && savedSignature.current === signature(values)) return current;
    return persist(values);
  };

  /** Exécute `task` sauf si une soumission est déjà en cours (ignorée, jamais doublée). */
  const exclusive = async (task: () => Promise<void>): Promise<void> => {
    if (locked.current) return;
    locked.current = true;
    setPending(true);
    try {
      await task();
    } finally {
      locked.current = false;
      setPending(false);
    }
  };

  return { persist, ensureSaved, exclusive, pending };
}
