/**
 * door-accounts: what an admin can do to dashboard accounts that the browser
 * cannot do by itself, because it needs Supabase's secret key.
 *
 *   POST {"action": "create",   "email", "password", "role": "admin"|"member"}
 *   POST {"action": "delete",   "user_id"}
 *   POST {"action": "password", "user_id", "password"}
 *
 * Every request carries the caller's own session ("Authorization: Bearer
 * <access token>", which supabase.functions.invoke() sends).  The first thing
 * done with it is to ask the database who that is (door_me(), run with the
 * caller's token, so a forged or expired token gets nowhere); only an admin
 * goes further.  Role changes themselves go through door_set_role() /
 * door_remove_member(), again with the caller's token, so the rules (no
 * self-change, one change at a time, the audit log) stay in one place: the
 * SQL in supabase/schema.sql.
 *
 * Answers are JSON: {"ok": true, ...} or {"ok": false, "error": "<code>"}.
 *
 * No imports: the same file runs on Supabase (Deno, via index.ts) and in the
 * tests (Node).
 */

export interface Failure {
  message?: string;
  code?: string;
  status?: number;
}

export interface Result<T> {
  data?: T | null;
  error?: Failure | null;
}

export interface Me {
  user_id: string;
  email: string | null;
  role: string | null;
}

/** What the handler may use; index.ts builds it from supabase-js. */
export interface Backend {
  /** A database function run AS THE CALLER: their token, their rules. */
  rpc(fn: string, args?: Record<string, unknown>): Promise<Result<unknown>>;
  /** Supabase Auth admin API, with the secret key. */
  createUser(email: string, password: string): Promise<Result<{ id: string }>>;
  deleteUser(id: string): Promise<Result<null>>;
  setPassword(id: string, password: string): Promise<Result<{ email: string }>>;
  /** One row into door_audit, with the secret key. */
  audit(row: Record<string, unknown>): Promise<Result<null>>;
}

export type Connect = (authorization: string, apikey: string) => Backend;

// The headers supabase-js may send (its own list: @supabase/supabase-js/cors);
// one missing here and the browser's preflight blocks the whole request.
export const CORS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type, x-retry-count, traceparent, tracestate, baggage",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ROLES = ["admin", "member"];
export const MIN_PASSWORD = 8;
const MAX_PASSWORD_BYTES = 72;            // Supabase Auth (bcrypt) ignores the rest

/** Errors raised by the SQL functions, and their HTTP status. */
const SQL_ERRORS: Record<string, number> = {
  not_admin: 403,
  self_change: 400,
  bad_role: 400,
  no_user: 404,
};

function reply(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

function fail(status: number, error: string): Response {
  return reply(status, { ok: false, error });
}

function sqlFailure(error: Failure | null | undefined): Response {
  const message = (error && error.message) || "";
  if (Object.hasOwn(SQL_ERRORS, message)) return fail(SQL_ERRORS[message], message);
  console.error("door-accounts: database", error);
  return fail(500, "server_error");
}

function authFailure(error: Failure | null | undefined): Response {
  const code = (error && error.code) || "";
  const message = ((error && error.message) || "").toLowerCase();
  if (code === "email_exists" || code === "user_already_exists" ||
      /already (been )?registered|already exists/.test(message)) {
    return fail(409, "email_exists");
  }
  if (code === "weak_password" || (/password/.test(message) && /weak|short|at least|characters/.test(message))) {
    return fail(400, "weak_password");
  }
  if (code === "user_not_found" || (error && error.status === 404)) return fail(404, "no_user");
  if (code === "email_address_invalid" || /invalid.*email|email.*invalid/.test(message)) {
    return fail(400, "bad_email");
  }
  console.error("door-accounts: auth", error);
  return fail(502, "auth_error");
}

function passwordOk(value: unknown): value is string {
  return typeof value === "string" && value.length >= MIN_PASSWORD &&
    new TextEncoder().encode(value).length <= MAX_PASSWORD_BYTES;
}

function userId(value: unknown): string {
  return typeof value === "string" && UUID.test(value) ? value.toLowerCase() : "";
}

async function create(backend: Backend, body: Record<string, unknown>): Promise<Response> {
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  if (!EMAIL.test(email) || email.length > 254) return fail(400, "bad_email");
  if (!passwordOk(body.password)) return fail(400, "weak_password");
  if (typeof body.role !== "string" || !ROLES.includes(body.role)) return fail(400, "bad_role");

  const made = await backend.createUser(email, body.password as string);
  if (made.error || !made.data || !made.data.id) return authFailure(made.error);
  const id = made.data.id;

  const granted = await backend.rpc("door_set_role", { p_user: id, p_role: body.role });
  if (granted.error) {
    await backend.deleteUser(id);         // no half-made accounts left behind
    return sqlFailure(granted.error);
  }
  return reply(200, { ok: true, user_id: id, email });
}

async function remove(backend: Backend, me: Me, body: Record<string, unknown>): Promise<Response> {
  const id = userId(body.user_id);
  if (!id) return fail(400, "bad_request");
  if (id === String(me.user_id).toLowerCase()) return fail(400, "self_change");

  // Access first: if deleting the login then fails, the account is already
  // harmless (a signed-in account without a role sees nothing).
  const revoked = await backend.rpc("door_remove_member", { p_user: id });
  if (revoked.error) return sqlFailure(revoked.error);

  const gone = await backend.deleteUser(id);
  if (gone.error && !(gone.error.status === 404 || gone.error.code === "user_not_found")) {
    console.error("door-accounts: delete", gone.error);
    return fail(502, "delete_failed");
  }
  return reply(200, { ok: true });
}

async function password(backend: Backend, me: Me, body: Record<string, unknown>): Promise<Response> {
  const id = userId(body.user_id);
  if (!id) return fail(400, "bad_request");
  if (!passwordOk(body.password)) return fail(400, "weak_password");

  const changed = await backend.setPassword(id, body.password as string);
  if (changed.error) return authFailure(changed.error);

  const logged = await backend.audit({
    actor: me.user_id,
    actor_email: me.email || "",
    action: "password.reset",
    target: id,
    target_email: (changed.data && changed.data.email) || "",
  });
  if (logged.error) console.error("door-accounts: audit", logged.error);
  return reply(200, { ok: true });
}

export async function handle(req: Request, connect: Connect): Promise<Response> {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (req.method !== "POST") return fail(405, "bad_request");

  const authorization = req.headers.get("Authorization") || "";
  if (!/^Bearer\s+\S+$/i.test(authorization)) return fail(401, "no_session");

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return fail(400, "bad_request");
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return fail(400, "bad_request");
  const request = body as Record<string, unknown>;

  try {
    const backend = connect(authorization, req.headers.get("apikey") || "");

    // Who is asking?  The database answers, from the caller's own token.
    const asked = await backend.rpc("door_me");
    if (asked.error || !asked.data) return fail(401, "no_session");
    const me = asked.data as Me;
    if (me.role !== "admin") return fail(403, "not_admin");

    switch (request.action) {
      case "create":
        return await create(backend, request);
      case "delete":
        return await remove(backend, me, request);
      case "password":
        return await password(backend, me, request);
      default:
        return fail(400, "bad_request");
    }
  } catch (err) {
    console.error("door-accounts", err);
    return fail(500, "server_error");
  }
}
