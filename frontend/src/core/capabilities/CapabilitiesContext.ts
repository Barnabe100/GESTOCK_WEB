import { createContext, useContext } from 'react';

import type { Capabilities } from '@/core/api/types';

export interface CapabilitiesContextValue {
  capabilities: Capabilities;
  can: (permission: string) => boolean;
  isRestricted: (permission: string) => boolean;
  hasModule: (code: string) => boolean;
  siteId: string | null;
  setSiteId: (siteId: string | null) => void;
}

export const CapabilitiesContext = createContext<CapabilitiesContextValue | null>(null);

export function useCapabilities(): CapabilitiesContextValue {
  const value = useContext(CapabilitiesContext);
  if (!value) throw new Error('useCapabilities doit être utilisé dans <CapabilitiesProvider>');
  return value;
}
