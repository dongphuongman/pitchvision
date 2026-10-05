/**
 * HAND-WRITTEN stand-in for the identity service's OpenAPI types, mirroring
 * `docs/contracts/identity-api.md` exactly. It uses the same `paths` / `components` shape that
 * openapi-typescript emits, so once `apps/identity/openapi.json` exists `pnpm gen` writes
 * `src/api/generated/identity.ts` and `identity.ts` only has to change its import line.
 *
 * Do not add endpoints here that are not in the contract.
 */

type NoParams = { query?: never; header?: never; path?: never; cookie?: never };
type PathParams<P> = { query?: never; header?: never; path: P; cookie?: never };
type QueryParams<Q> = { query: Q; header?: never; path?: never; cookie?: never };
type Json<T> = { headers: { [name: string]: unknown }; content: { 'application/json': T } };
type NoContent = { headers: { [name: string]: unknown }; content?: never };
type JsonBody<T> = { content: { 'application/json': T } };
type ProblemResponse = {
  headers: { [name: string]: unknown };
  content: { 'application/problem+json': components['schemas']['Problem'] };
};

/** Operation shaped like openapi-typescript output (`requestBody?: never` when there is none). */
type Op<P, B, R> = {
  parameters: P;
  responses: R & { default: ProblemResponse };
} & ([B] extends [undefined] ? { requestBody?: never } : { requestBody: B });

/** Operation whose JSON body is optional (`POST /auth/refresh`). */
type OptionalBodyOp<P, B, R> = {
  parameters: P;
  requestBody?: B;
  responses: R & { default: ProblemResponse };
};

type S = components['schemas'];

export interface paths {
  '/api/v1/auth/register': {
    parameters: NoParams;
    post: Op<NoParams, JsonBody<S['RegisterRequest']>, { 201: Json<S['Session']> }>;
  };
  '/api/v1/auth/login': {
    parameters: NoParams;
    post: Op<NoParams, JsonBody<S['LoginRequest']>, { 200: Json<S['Session']> }>;
  };
  '/api/v1/auth/refresh': {
    parameters: NoParams;
    post: OptionalBodyOp<NoParams, JsonBody<S['RefreshRequest']>, { 200: Json<S['Session']> }>;
  };
  '/api/v1/auth/logout': {
    parameters: NoParams;
    post: Op<NoParams, undefined, { 204: NoContent }>;
  };
  '/api/v1/auth/verify-email': {
    parameters: NoParams;
    post: Op<NoParams, JsonBody<S['VerifyEmailRequest']>, { 200: Json<S['Session']> }>;
  };
  '/api/v1/auth/verify-email/resend': {
    parameters: NoParams;
    post: Op<NoParams, undefined, { 202: NoContent }>;
  };
  '/api/v1/auth/password/forgot': {
    parameters: NoParams;
    post: Op<NoParams, JsonBody<S['ForgotPasswordRequest']>, { 202: NoContent }>;
  };
  '/api/v1/auth/password/reset': {
    parameters: NoParams;
    post: Op<NoParams, JsonBody<S['ResetPasswordRequest']>, { 204: NoContent }>;
  };
  '/api/v1/auth/oidc/google/start': {
    parameters: QueryParams<{ return_to?: string }>;
    get: Op<QueryParams<{ return_to?: string }>, undefined, { 302: NoContent }>;
  };
  '/api/v1/me': {
    parameters: NoParams;
    get: Op<NoParams, undefined, { 200: Json<S['Me']> }>;
  };
  '/api/v1/me/unverified': {
    parameters: NoParams;
    delete: Op<NoParams, undefined, { 204: NoContent }>;
  };
  '/api/v1/orgs': {
    parameters: NoParams;
    post: Op<NoParams, JsonBody<S['CreateOrgRequest']>, { 201: Json<S['Org']> }>;
  };
  '/api/v1/orgs/{id}': {
    parameters: PathParams<{ id: string }>;
    get: Op<PathParams<{ id: string }>, undefined, { 200: Json<S['Org']> }>;
    patch: Op<PathParams<{ id: string }>, JsonBody<S['UpdateOrgRequest']>, { 200: Json<S['Org']> }>;
  };
  '/api/v1/orgs/{id}/members': {
    parameters: PathParams<{ id: string }>;
    get: Op<PathParams<{ id: string }>, undefined, { 200: Json<S['Member'][]> }>;
  };
  '/api/v1/orgs/{id}/members/{user_id}': {
    parameters: PathParams<{ id: string; user_id: string }>;
    patch: Op<
      PathParams<{ id: string; user_id: string }>,
      JsonBody<S['UpdateMemberRequest']>,
      { 200: Json<S['Member']> }
    >;
    delete: Op<PathParams<{ id: string; user_id: string }>, undefined, { 204: NoContent }>;
  };
  '/api/v1/orgs/{id}/invites': {
    parameters: PathParams<{ id: string }>;
    get: Op<PathParams<{ id: string }>, undefined, { 200: Json<S['Invite'][]> }>;
    post: Op<
      PathParams<{ id: string }>,
      JsonBody<S['CreateInvitesRequest']>,
      { 201: Json<S['Invite'][]> }
    >;
  };
  '/api/v1/orgs/{id}/invites/{invite_id}': {
    parameters: PathParams<{ id: string; invite_id: string }>;
    delete: Op<PathParams<{ id: string; invite_id: string }>, undefined, { 204: NoContent }>;
  };
  '/api/v1/orgs/{id}/invites/{invite_id}/resend': {
    parameters: PathParams<{ id: string; invite_id: string }>;
    post: Op<PathParams<{ id: string; invite_id: string }>, undefined, { 202: NoContent }>;
  };
  '/api/v1/invites/{token}': {
    parameters: PathParams<{ token: string }>;
    get: Op<PathParams<{ token: string }>, undefined, { 200: Json<S['InvitePreview']> }>;
  };
  '/api/v1/invites/{token}/accept': {
    parameters: PathParams<{ token: string }>;
    post: Op<PathParams<{ token: string }>, undefined, { 200: Json<S['Session']> }>;
  };
}

export interface components {
  schemas: {
    /** @enum {string} */
    Role: 'owner' | 'admin' | 'editor' | 'viewer';
    /** @enum {string} */
    Region: 'eu' | 'us';
    /** @enum {string} */
    OrgKind: 'club' | 'academy' | 'media' | 'agency' | 'individual' | 'other';
    User: {
      id: string;
      name: string;
      email: string;
      email_verified: boolean;
      /** Format: date-time */
      created_at: string;
    };
    Membership: {
      org_id: string;
      org_name: string;
      role: S['Role'];
      region: S['Region'];
    };
    Session: {
      access_token: string;
      token_type: 'bearer';
      /** Seconds until `access_token` expires (15 min). */
      expires_in: number;
      user: S['User'];
      memberships: S['Membership'][];
      active_org_id: string | null;
    };
    Me: S['User'] & { memberships: S['Membership'][] };
    Org: {
      id: string;
      name: string;
      kind: S['OrgKind'];
      region: S['Region'];
      /** Format: date-time */
      created_at: string;
    };
    Member: {
      user_id: string;
      name: string;
      email: string;
      role: S['Role'];
      /** Format: date-time */
      joined_at: string;
    };
    Invite: {
      id: string;
      email: string;
      role: S['Role'];
      /** Format: date-time */
      expires_at: string;
      /** Format: date-time */
      created_at: string;
      invited_by: string;
    };
    InvitePreview: {
      org_name: string;
      inviter_name: string;
      email: string;
      role: S['Role'];
      /** Added by the identity service (clarification during W2): where the org's data lives. */
      region: S['Region'];
      expired: boolean;
    };
    RegisterRequest: {
      name: string;
      email: string;
      /** Min 10 characters. */
      password: string;
      invite_token?: string;
      plan?: string;
    };
    LoginRequest: { email: string; password: string };
    RefreshRequest: { org_id?: string };
    VerifyEmailRequest: { token: string };
    ForgotPasswordRequest: { email: string };
    ResetPasswordRequest: { token: string; password: string };
    CreateOrgRequest: {
      name: string;
      kind: S['OrgKind'];
      my_role_title?: string;
      region: S['Region'];
    };
    UpdateOrgRequest: { name?: string };
    UpdateMemberRequest: { role: S['Role'] };
    CreateInvitesRequest: {
      emails: string[];
      role: 'viewer' | 'editor' | 'admin';
      message?: string;
    };
    /** RFC 9457 problem details with the PitchVision `code`. */
    Problem: {
      type: string;
      title: string;
      status: number;
      code: string;
      detail?: string;
      instance?: string;
    };
  };
}
