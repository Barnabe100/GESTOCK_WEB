import { useQuery } from '@tanstack/react-query';

import { api } from './client';

/** Pays du référentiel (ISO 3166-1), servis par l'API — jamais embarqués dans l'interface. */
export interface PublicCountry {
  code: string;
  name: string;
  currency: string;
  calling_code: number | null;
  timezone: string;
}

export function usePublicCountries() {
  return useQuery({
    queryKey: ['public', 'countries'],
    queryFn: ({ signal }) => api.get<PublicCountry[]>('/public/geo/countries', signal),
    staleTime: Infinity,
    retry: 1,
  });
}
