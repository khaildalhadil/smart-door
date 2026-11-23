/** One row of door_events (see supabase/schema.sql). */
export interface DoorEvent {
  uid: string;
  ts: string;
  category: string;   // entry, denied, doorbell, alarm, remote, admin, system
  method: string;     // keypad, face, fingerprint, card, temp_code, whatsapp, doorbell ...
  person: string;
  event: string;      // event-log name: lock.unlock, keypad.deny ...
  detail: string;
  level: string;
  photo: string;      // path in the door-photos bucket ("data:" URL in the demo), "" = none
  t?: number;         // ts in milliseconds, added on load
}

/** The door_status heartbeat row. */
export interface DoorStatus {
  id: string;
  updated_at: string;
  lock: string;
  alarm: string;
  camera: boolean | null;
  bot: boolean | null;
  info: Record<string, unknown>;
}

export type Role = "admin" | "member";

export interface Me {
  user_id: string;
  email: string;
  role: Role | null;
}

export interface Account {
  user_id: string;
  email: string;
  role: Role | null;
  is_me?: boolean;
  created_at?: string;
  last_sign_in_at?: string | null;
}

export interface AuditRow {
  id: number;
  ts: string;
  actor_email: string;
  action: string;
  target_email: string;
  old_role: string | null;
  new_role: string | null;
}

/** What the dashboard reads and does; the Supabase client and the demo both provide it. */
export interface Source {
  readonly demo: boolean;
  me(): Promise<Me | null>;
  events(fromISO: string): Promise<DoorEvent[]>;
  count(category: string, fromISO: string, toISO: string): Promise<number | null>;
  status(): Promise<DoorStatus | null>;
  sign(paths: string[]): Promise<Record<string, string>>;
  subscribe(onEvent: (row: DoorEvent) => void, onStatus: (s: DoorStatus) => void,
            onLive: (live: boolean) => void): void;
  unsubscribe(): void;
  signOut(): Promise<void>;
  changePassword(password: string): Promise<void>;
  members(): Promise<Account[]>;
  auditLog(limit: number): Promise<AuditRow[]>;
  setRole(userId: string, role: Role): Promise<void>;
  createAccount(email: string, password: string, role: Role): Promise<unknown>;
  deleteAccount(userId: string): Promise<unknown>;
  resetPassword(userId: string, password: string): Promise<unknown>;
}
