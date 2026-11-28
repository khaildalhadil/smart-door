import { createClient, type RealtimeChannel, type SupabaseClient } from "@supabase/supabase-js";
import { CFG, MAX_PAGES, PAGE, SIGN_SECONDS } from "./config";
import type { Account, AuditRow, ChatMessage, DoorEvent, DoorStatus, Me, Role, Source } from "./types";

const COLUMNS = "uid,ts,category,method,person,event,detail,level,photo";
const CHAT_COLUMNS = "id,created_at,sender,body,texts,photo,intent,status,reply_to";

type Factory = typeof createClient;
declare global {
  interface Window {
    /** A supabase-js loaded with a <script> tag (the tests use a stand-in). */
    supabase?: { createClient: Factory };
  }
}

/** The door's data in Supabase.  Row-level security decides what comes back. */
export class CloudSource implements Source {
  readonly demo = false;
  readonly client: SupabaseClient;
  private channel: RealtimeChannel | null = null;

  constructor(onSignedOut: () => void) {
    const make: Factory = window.supabase?.createClient ?? createClient;
    this.client = make(CFG.url, CFG.key, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
    });
    // Signed out in another tab, or the account was deleted: back to the form.
    this.client.auth.onAuthStateChange((event) => {
      if (event === "SIGNED_OUT") setTimeout(onSignedOut, 0);
    });
  }

  async hasSession(): Promise<boolean> {
    const { data } = await this.client.auth.getSession();
    return Boolean(data?.session);
  }

  async signIn(email: string, password: string): Promise<void> {
    const { error } = await this.client.auth.signInWithPassword({ email, password });
    if (error) throw error;
  }

  async signOut(): Promise<void> {
    await this.client.auth.signOut();
  }

  /** { user_id, email, role }: role is "admin", "member" or null (no access). */
  async me(): Promise<Me | null> {
    const { data, error } = await this.client.rpc("door_me");
    if (error) throw error;
    return data as Me | null;
  }

  async changePassword(password: string): Promise<void> {
    const { error } = await this.client.auth.updateUser({ password });
    if (error) throw error;
  }

  // Admins only; the database refuses everyone else.
  async members(): Promise<Account[]> {
    const { data, error } = await this.client.rpc("door_list_members");
    if (error) throw error;
    return (data as Account[]) || [];
  }

  async auditLog(limit: number): Promise<AuditRow[]> {
    const { data, error } = await this.client.from("door_audit")
      .select("id,ts,actor_email,action,target_email,old_role,new_role")
      .order("ts", { ascending: false }).limit(limit);
    if (error) throw error;
    return (data as AuditRow[]) || [];
  }

  async setRole(userId: string, role: Role): Promise<void> {
    const { error } = await this.client.rpc("door_set_role", { p_user: userId, p_role: role });
    if (error) throw error;
  }

  /** The door-accounts Edge Function: things that need Supabase's secret key. */
  private async accounts(body: Record<string, unknown>): Promise<unknown> {
    const { data, error } = await this.client.functions.invoke("door-accounts", { body });
    if (!error) return data;
    let code = "";
    const response = (error as { context?: Response }).context;
    if (response && typeof response.json === "function") {
      try {
        const payload = await response.json();
        code = (payload && payload.error) || "";
      } catch {
        /* not JSON */
      }
      if (!code && response.status === 404) code = "function_missing";
    }
    throw new Error(code || error.message);
  }

  createAccount(email: string, password: string, role: Role) {
    return this.accounts({ action: "create", email, password, role });
  }

  deleteAccount(userId: string) {
    return this.accounts({ action: "delete", user_id: userId });
  }

  resetPassword(userId: string, password: string) {
    return this.accounts({ action: "password", user_id: userId, password });
  }

  async events(fromISO: string): Promise<DoorEvent[]> {
    let out: DoorEvent[] = [];
    for (let page = 0; page < MAX_PAGES; page++) {
      const { data, error } = await this.client.from(CFG.table).select(COLUMNS).gte("ts", fromISO)
        .order("ts", { ascending: false }).range(page * PAGE, page * PAGE + PAGE - 1);
      if (error) throw error;
      out = out.concat(data as DoorEvent[]);
      if (data.length < PAGE) break;
    }
    return out;
  }

  async count(category: string, fromISO: string, toISO: string): Promise<number | null> {
    const { count, error } = await this.client.from(CFG.table).select("uid", { count: "exact", head: true })
      .eq("category", category).gte("ts", fromISO).lt("ts", toISO);
    if (error) throw error;
    return count;
  }

  async status(): Promise<DoorStatus | null> {
    const { data, error } = await this.client.from(CFG.statusTable).select("*").eq("id", "door").maybeSingle();
    if (error) throw error;
    return data as DoorStatus | null;
  }

  async sign(paths: string[]): Promise<Record<string, string>> {
    const { data, error } = await this.client.storage.from(CFG.bucket).createSignedUrls(paths, SIGN_SECONDS);
    if (error) throw error;
    const map: Record<string, string> = {};
    for (const item of data) if (item.signedUrl && item.path) map[item.path] = item.signedUrl;
    return map;
  }

  subscribe(onEvent: (row: DoorEvent) => void, onStatus: (s: DoorStatus) => void,
            onLive: (live: boolean) => void): void {
    this.channel = this.client.channel("door-dashboard")
      .on("postgres_changes", { event: "INSERT", schema: "public", table: CFG.table },
          (payload) => onEvent(payload.new as DoorEvent))
      .on("postgres_changes", { event: "*", schema: "public", table: CFG.statusTable },
          (payload) => {
            const row = payload.new as DoorStatus;
            if (row && row.id) onStatus(row);
          })
      .subscribe((status) => onLive(status === "SUBSCRIBED"));
  }

  unsubscribe(): void {
    if (this.channel) this.client.removeChannel(this.channel);
    this.channel = null;
  }

  // ------------------------------------------------- chat with the door (admins)
  // The Pi (app/webchat.py) answers each message; row-level security shows an
  // admin their own conversation only.
  async chatList(limit: number): Promise<ChatMessage[]> {
    const { data, error } = await this.client.from(CFG.chatTable).select(CHAT_COLUMNS)
      .order("id", { ascending: false }).limit(limit);
    if (error) throw error;
    return ((data as ChatMessage[]) || []).reverse();
  }

  async chatSend(text: string): Promise<ChatMessage> {
    const { data, error } = await this.client.from(CFG.chatTable)
      .insert({ sender: "admin", body: text }).select(CHAT_COLUMNS).single();
    if (error) throw error;
    return data as ChatMessage;
  }

  async chatClear(): Promise<void> {
    const { data } = await this.client.auth.getUser();
    if (!data?.user) throw new Error("no_session");
    const { error } = await this.client.from(CFG.chatTable).delete().eq("user_id", data.user.id);
    if (error) throw error;
  }

  chatSubscribe(onChange: (m: ChatMessage) => void): () => void {
    const channel = this.client.channel("door-chat")
      .on("postgres_changes", { event: "*", schema: "public", table: CFG.chatTable }, (payload) => {
        const row = payload.new as ChatMessage;
        if (row && row.id) onChange(row);
      })
      .subscribe();
    return () => { void this.client.removeChannel(channel); };
  }
}
