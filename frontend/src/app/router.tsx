import { createBrowserRouter } from 'react-router';

import { ChangePasswordPage } from '@/pages/ChangePasswordPage';
import { LoginPage } from '@/pages/LoginPage';
import { SelectTenantPage } from '@/pages/SelectTenantPage';
import { SignupPage } from '@/pages/SignupPage';

import { ProtectedApp } from './ProtectedApp';

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  // Inscription publique : nouveau compte + nouvelle entreprise (redirigée si connecté).
  { path: '/signup', element: <SignupPage /> },
  { path: '/change-password', element: <ChangePasswordPage /> },
  { path: '/select-tenant', element: <SelectTenantPage /> },
  // Toutes les autres routes : application authentifiée, routes générées dynamiquement.
  { path: '/*', element: <ProtectedApp /> },
]);
