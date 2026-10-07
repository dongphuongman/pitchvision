/**
 * Compile-time guard: the hand-written identity client types (identity-contract.ts) must stay
 * mutually assignable with the types generated from apps/identity/openapi.json. `tsc` fails on drift.
 */
import type { components as Hand } from './identity-contract';
import type { components as Gen } from './generated/identity';

type H = Hand['schemas'];
type G = Gen['schemas'];
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
type Assert<T extends true> = T;

export type IdentityContractInSync = [
  Assert<Same<H['User'], G['User']>>,
  Assert<Same<H['Membership'], G['Membership']>>,
  Assert<Same<H['Session'], G['Session']>>,
  Assert<Same<H['Me'], G['Me']>>,
  Assert<Same<H['Org'], G['Org']>>,
  Assert<Same<H['Member'], G['Member']>>,
  Assert<Same<H['Invite'], G['Invite']>>,
  Assert<Same<H['InvitePreview'], G['InvitePreview']>>,
  Assert<Same<H['ChangePasswordRequest'], G['PasswordChangeIn']>>,
  Assert<Same<H['UpgradeRequestOut'], G['UpgradeRequestOut']>>,
  Assert<Same<H['DeletionPreview'], G['DeletionPreview']>>,
  Assert<Same<H['AnalysisDefaults'], G['AnalysisDefaults']>>,
  Assert<Same<H['DeleteOrgRequest'], G['OrgDeleteIn']>>,
  Assert<Same<H['DeleteMeRequest'], G['MeDeleteIn']>>,
];
