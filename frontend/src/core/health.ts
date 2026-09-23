import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';

import { apiGet } from '@/core/apiClient';

export const healthSchema = z.object({
  status: z.string(),
  version: z.string(),
});

export type Health = z.infer<typeof healthSchema>;

export function useHealth() {
  return useQuery({
    queryKey: ['system', 'health'],
    queryFn: async ({ signal }) => healthSchema.parse(await apiGet<unknown>('/health', signal)),
  });
}
