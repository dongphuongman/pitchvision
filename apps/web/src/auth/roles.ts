import type { Role } from '@/api/identity';

/** viewer < editor < admin < owner (AD / contract: `require_role`). */
const RANK: Record<Role, number> = { viewer: 0, editor: 1, admin: 2, owner: 3 };

/** True when `role` is at least `min`. No role (signed out / no org) can do nothing. */
export function can(role: Role | null | undefined, min: Role): boolean {
  return role ? RANK[role] >= RANK[min] : false;
}
