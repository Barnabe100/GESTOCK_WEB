import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';

export interface RoleAssignment {
  role_id: string;
  site_id: string | null;
}

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

export interface Role {
  id: string;
  name: string;
  description: string | null;
  template_code: string | null;
  is_system: boolean;
  permission_codes: string[];
}

export interface RoleInput {
  name: string;
  description?: string | null;
  permissions: string[];
}

export interface Permission {
  code: string;
  module: string;
  access: string;
}

export const userKeys = {
  members: ['users', 'members'] as const,
  roles: ['users', 'roles'] as const,
  permissions: ['users', 'permissions'] as const,
};

export function useMembers() {
  return useQuery({
    queryKey: userKeys.members,
    queryFn: ({ signal }) => api.get<Member[]>('/members', signal),
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

export function usePermissions() {
  return useQuery({
    queryKey: userKeys.permissions,
    queryFn: ({ signal }) => api.get<Permission[]>('/permissions', signal),
  });
}

export function useSaveRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string; input: RoleInput }) =>
      id ? api.patch<Role>(`/roles/${id}`, input) : api.post<Role>('/roles', input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.roles });
      void qc.invalidateQueries({ queryKey: ['capabilities'] });
    },
  });
}

export function useDeleteRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/roles/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: userKeys.roles }),
  });
}
