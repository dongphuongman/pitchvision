import type { ReactNode } from 'react';
import type { Role } from '@/api/identity';
import { can } from './roles';
import { useRole } from './session';

/**
 * Renders `children` only when the active-org role is at least `min` (UIUX §3.1: elements the
 * user may not use are hidden, not disabled). The API enforces the same rule server-side.
 */
export function RequireRole({
  min,
  children,
  fallback = null,
}: {
  min: Role;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const role = useRole();
  return <>{can(role, min) ? children : fallback}</>;
}
