import { createContext, useContext } from 'react';

import type { MembershipSummary, SignupInput, UserInfo } from '@/core/api/types';

export type AuthStatus = 'loading' | 'anonymous' | 'authenticated';

export interface AuthState {
  status: AuthStatus;
  user: UserInfo | null;
  tenantId: string | null;
  memberships: MembershipSummary[];
}

export interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  /** Inscription publique : crée compte + entreprise et ouvre la session sur celle-ci. */
  signup: (input: SignupInput) => Promise<void>;
  logout: () => Promise<void>;
  selectTenant: (tenantId: string) => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth doit être utilisé dans <AuthProvider>');
  return value;
}
