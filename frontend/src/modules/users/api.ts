import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

export interface RoleAssignment {
  role_id: string;
  site_id: string | null;
}

/**
 * Appartenance au tenant (ressource administrée). `email` et `full_name` appartiennent au compte
 * global de l'utilisateur : lecture seule ici (ADR-0029). `created_at` : ajout au tenant.
 */
export interface Member {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  status: 'active' | 'suspended';
  is_owner: boolean;
  all_sites: boolean;
  must_change_password: boolean;
  roles: RoleAssignment[];
  site_ids: string[];
  created_at: string;
}

export interface MemberCreateInput {
  email: string;
  full_name: string;
  password?: string;
  roles: { role_id: string }[];
  site_ids: string[];
  all_sites: boolean;
}

export interface MemberUpdateInput {
  roles?: RoleAssignment[];
  site_ids?: string[];
  all_sites?: boolean;
  status?: 'active' | 'suspended';
}

/** Rôle de base (système, lecture seule) ou rôle personnalisé du tenant. */
export interface Role {
  id: string;
  name: string;
  description: string | null;
  template_code: string | null;
  is_system: boolean;
  is_active: boolean;
  /** Rôle protégé (Administrateur) : ni désactivable ni modifiable. */
  protected: boolean;
  member_count: number;
  permission_codes: string[];
  /**
   * Calculé par le serveur : l'utilisateur courant peut accorder ce rôle sur tout le tenant (et,
   * s'il est personnalisé, le modifier, le dupliquer, l'activer ou le désactiver). ADR-0030.
   */
  delegable: boolean;
}

export interface RoleMember {
  membership_id: string;
  user_id: string;
  full_name: string;
  email: string;
  status: 'active' | 'suspended';
  site_id: string | null;
}

export interface RoleInput {
  name: string;
  description?: string | null;
  permissions: string[];
}

/** Permission déclarée par un module de l'offre (liste fournie par l'API, jamais figée). */
export interface Permission {
  code: string;
  module: string;
  access: string;
  resource: string;
  action: string;
}

export const userKeys = {
  members: ['users', 'members'] as const,
  roles: ['users', 'roles'] as const,
  permissions: ['users', 'permissions'] as const,
};

/** Liste paginée côté serveur : recherche (nom, e-mail), statut, rôle, site, tri. */
export function useMembers(query: string) {
  return useQuery({
    queryKey: [...userKeys.members, 'list', query],
    queryFn: ({ signal }) => api.get<Page<Member>>(`/members?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

/** Activer / désactiver l'appartenance à CE tenant (jamais le compte global). */
export function useSetMemberActive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api.post<Member>(`/members/${id}/${active ? 'activate' : 'deactivate'}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: userKeys.members }),
  });
}

export function useSaveMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (
      args: { id: string; input: MemberUpdateInput } | { id?: undefined; input: MemberCreateInput },
    ) =>
      args.id
        ? api.patch<Member>(`/members/${args.id}`, args.input)
        : api.post<Member>('/members', args.input),
    onSuccess: () => void qc.invalidateQueries({ queryKey: userKeys.members }),
  });
}

export function useRoles() {
  return useQuery({
    queryKey: userKeys.roles,
    queryFn: ({ signal }) => api.get<Role[]>('/roles', signal),
  });
}

/**
 * Délégation calculée par le serveur (jamais par l'interface) : permissions et rôles actifs que
 * l'utilisateur courant peut accorder sur tout le tenant, ou pour un site de son périmètre.
 */
export function useDelegablePermissions(siteId: string | null = null, enabled = true) {
  return useQuery({
    queryKey: [...userKeys.roles, 'delegable-permissions', siteId],
    queryFn: ({ signal }) =>
      api.get<Permission[]>(`/permissions/delegable${siteId ? `?site_id=${siteId}` : ''}`, signal),
    enabled,
  });
}

export function useDelegableRoles(siteId: string | null = null, enabled = true) {
  return useQuery({
    queryKey: [...userKeys.roles, 'delegable', siteId],
    queryFn: ({ signal }) =>
      api.get<Role[]>(`/roles/delegable${siteId ? `?site_id=${siteId}` : ''}`, signal),
    enabled,
  });
}

export function usePermissions() {
  return useQuery({
    queryKey: userKeys.permissions,
    queryFn: ({ signal }) => api.get<Permission[]>('/permissions', signal),
  });
}

function useRoleMutation<TArgs, TResult>(mutationFn: (args: TArgs) => Promise<TResult>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.roles });
      void qc.invalidateQueries({ queryKey: userKeys.members });
      // Les droits de l'utilisateur courant peuvent changer.
      void qc.invalidateQueries({ queryKey: ['capabilities'] });
    },
  });
}

export function useSaveRole() {
  return useRoleMutation(({ id, input }: { id?: string; input: RoleInput }) =>
    id ? api.patch<Role>(`/roles/${id}`, input) : api.post<Role>('/roles', input),
  );
}

export function useDuplicateRole() {
  return useRoleMutation(
    ({ id, name, description }: { id: string; name: string; description: string | null }) =>
      api.post<Role>(`/roles/${id}/duplicate`, { name, description }),
  );
}

/** Désactivation : sans `confirm`, l'API répond 409 `role_in_use` si le rôle est attribué. */
export function useSetRoleActive() {
  return useRoleMutation(
    ({ id, active, confirm = false }: { id: string; active: boolean; confirm?: boolean }) =>
      active
        ? api.post<Role>(`/roles/${id}/activate`)
        : api.post<Role>(`/roles/${id}/deactivate`, { confirm }),
  );
}

export function useRoleMembers(id: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: [...userKeys.roles, id, 'members'],
    queryFn: ({ signal }) => api.get<RoleMember[]>(`/roles/${id}/members`, signal),
    enabled: enabled && id !== undefined,
  });
}

export interface RoleTemplate {
  code: string;
  name: string;
  description: string | null;
  instantiated: boolean;
}

export function useRoleTemplates(enabled: boolean) {
  return useQuery({
    queryKey: [...userKeys.roles, 'templates'],
    queryFn: ({ signal }) => api.get<RoleTemplate[]>('/role-templates', signal),
    enabled,
  });
}

export function useCreateRoleFromTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (templateCode: string) =>
      api.post<Role>('/roles/from-template', { template_code: templateCode }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: userKeys.roles }),
  });
}
