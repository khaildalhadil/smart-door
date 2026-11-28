/** Made-up history, so the dashboard can be seen without Supabase.
 *  Accounts live in memory under the same rules as the database. */
import { fmtTime, startOfDay } from "./format";
import { EMAIL, MIN_PASSWORD } from "./passwords";
import type { Account, AuditRow, ChatMessage, DoorEvent, DoorStatus, Me, Role, Source } from "./types";
import { visibleTo } from "./wording";

let seedValue = 20261113;
/** mulberry32: the same "history" on every load. */
function random(): number {
  seedValue |= 0;
  seedValue = (seedValue + 0x6d2b79f5) | 0;
  let t = Math.imul(seedValue ^ (seedValue >>> 15), 1 | seedValue);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
const pick = <T,>(list: T[]): T => list[Math.floor(random() * list.length)];

function picture(category: string, t: number): string {
  const tint = ({ entry: "#3f6f58", denied: "#7a4040", doorbell: "#3d5a80", alarm: "#8a5a2b" } as
    Record<string, string>)[category] || "#555";
  const shift = Math.round(random() * 60 - 30);
  const markup =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 240">' +
    '<rect width="320" height="240" fill="#2b3038"/><rect y="170" width="320" height="70" fill="#22262d"/>' +
    `<circle cx="${160 + shift}" cy="96" r="38" fill="#c9a98f"/>` +
    `<rect x="${100 + shift}" y="140" width="120" height="100" rx="18" fill="${tint}"/>` +
    `<text x="12" y="228" font-family="monospace" font-size="15" fill="#fff">${fmtTime(t)}</text></svg>`;
  return "data:image/svg+xml," + encodeURIComponent(markup);
}

let rows: DoorEvent[] | null = null;

function build(): DoorEvent[] {
  const out: DoorEvent[] = [];
  const now = Date.now();
  let id = 0;
  function push(t: number, category: string, method: string, person: string, event: string,
                detail: string, photo: boolean) {
    if (t > now) return;
    out.push({
      uid: "demo" + ++id, ts: new Date(t).toISOString(), category, method, person, event, detail,
      level: category === "denied" ? "WARNING" : "INFO", photo: photo ? picture(category, t) : "",
    });
  }
  const hours = [6, 7, 7, 8, 12, 13, 14, 16, 17, 18, 18, 19, 19, 20, 21, 22];
  for (let back = 95; back >= 0; back--) {
    const day = startOfDay(now).getTime() - back * 86400000;
    const weekday = new Date(day).getDay();
    const visits = 3 + Math.floor(random() * 5) + (weekday === 5 ? 3 : 0);
    for (let i = 0; i < visits; i++) {
      const t = day + pick(hours) * 3600000 + Math.floor(random() * 3600000);
      const roll = random();
      if (roll < 0.42) {
        const who = random() < 0.7 ? "Khalid" : "Sara";
        push(t, "entry", "face", who, "lock.unlock", `5.0s (face ${who} 0.7${Math.floor(random() * 9)})`, true);
      } else if (roll < 0.58) {
        push(t, "entry", "keypad", "", "lock.unlock", "5.0s (keypad)", true);
      } else if (roll < 0.7) {
        const finger = random() < 0.6 ? "Khalid" : "Ahmed";
        push(t, "entry", "fingerprint", finger, "lock.unlock", `5.0s (fingerprint ${finger} slot 1)`, true);
      } else if (roll < 0.82) {
        const holder = random() < 0.5 ? "Sara" : "Ahmed";
        push(t, "entry", "card", holder, "lock.unlock", `5.0s (card ${holder})`, true);
      } else if (roll < 0.87) {
        push(t - 600000, "remote", "temp_code", "", "tempcode.add", "#a1b2 valid 600s", false);
        push(t, "entry", "temp_code", "", "lock.unlock", "5.0s (temp code)", true);
      } else if (roll < 0.93) {
        push(t - 2000, "remote", "whatsapp", "", "bot.command", "open from …4821", false);
        push(t, "entry", "whatsapp", "", "lock.unlock", "5.0s (whatsapp)", true);
      } else {
        const wait = 8000 + Math.floor(random() * 50000);
        push(t - wait, "doorbell", "doorbell", "", "doorbell.ring", "bell pressed on the door screen", true);
        push(t, "entry", "doorbell", "", "lock.unlock", "5.0s (whatsapp doorbell)", true);
        push(t + 300, "doorbell", "doorbell", "", "doorbell.answer", "owner opened the door (…4821)", false);
      }
    }
    const refused = random() < 0.45 ? 1 + Math.floor(random() * 2) : 0;
    for (let j = 0; j < refused; j++) {
      const at = day + pick([1, 3, 9, 15, 19, 20, 23]) * 3600000 + Math.floor(random() * 3600000);
      const kind = random();
      if (kind < 0.45) {
        push(at, "denied", "keypad", "", "keypad.deny", "wrong passcode (2 attempt(s) left)", true);
        if (random() < 0.25) {
          push(at + 9000, "denied", "keypad", "", "keypad.deny", "wrong passcode (1 attempt(s) left)", true);
          push(at + 17000, "denied", "keypad", "", "keypad.lockout", "too many failures, locked out for 30s", true);
        }
      } else if (kind < 0.7) {
        push(at, "denied", "face", "", "face.deny", "unknown face (closest Khalid 0.21)", true);
      } else if (kind < 0.85) {
        push(at, "denied", "fingerprint", "", "fingerprint.deny", "search: no matching fingerprint found (0x09)", true);
      } else {
        push(at, "denied", "card", "", "card.deny", "unknown card …3F2A (2 attempt(s) left)", true);
      }
    }
    if (random() < 0.22) {
      const ring = day + pick([10, 11, 16, 17, 20]) * 3600000 + Math.floor(random() * 3600000);
      push(ring, "doorbell", "doorbell", "", "doorbell.ring", "bell pressed on the door screen", true);
      if (random() < 0.5) push(ring + 21000, "doorbell", "doorbell", "", "doorbell.answer", "owner declined (…4821)", false);
      else push(ring + 120000, "doorbell", "doorbell", "", "doorbell.timeout", "no answer within 120s", false);
    }
    if (back === 9) {
      const fire = day + 15 * 3600000 + 1260000;
      push(fire, "alarm", "fire", "", "hazard.fire", "FIRE DETECTED on GPIO5", true);
      push(fire + 200, "alarm", "fire", "", "lock.unlock", "10.0s (fire alarm)", false);
      push(fire + 48000, "alarm", "fire", "", "hazard.fire", "fire alarm over (quiet for 15s)", false);
    }
    if (back === 4) {
      const panel = day + 18 * 3600000 + 600000;
      push(panel, "admin", "login", "Khalid", "session.login", "Khalid (admin)", true);
      push(panel + 40000, "admin", "", "Ahmed", "card.add", "Ahmed (card …77C1)", true);
    }
    if (back % 13 === 0) push(day + 6 * 3600000, "system", "", "", "app.start", "config=config.ini", false);
  }
  out.sort((a, c) => (a.ts < c.ts ? 1 : -1));
  return out;
}

const all = () => (rows ??= build());
const iso = (t: number) => new Date(t).toISOString();
const later = <T,>(value?: T) => new Promise<T>((resolve) => setTimeout(() => resolve(value as T), 200));
function need(ok: unknown, code: string): asserts ok {
  if (!ok) throw new Error(code);
}

export class DemoSource implements Source {
  readonly demo = true;
  private accounts: Account[];
  private changes: AuditRow[];
  private chat: ChatMessage[] = [];
  private chatId = 0;
  private chatListeners = new Set<(m: ChatMessage) => void>();

  constructor(private role: Role) {
    const now = Date.now(), day = 86400000;
    this.accounts = [
      { user_id: "demo-khalid", email: "khalid@example.com", role: "admin",
        created_at: iso(now - 60 * day), last_sign_in_at: iso(now - 4 * 60000) },
      { user_id: "demo-sara", email: "sara@example.com", role: "member",
        created_at: iso(now - 21 * day), last_sign_in_at: iso(now - 26 * 3600000) },
      { user_id: "demo-ahmed", email: "ahmed@example.com", role: "member",
        created_at: iso(now - 9 * day), last_sign_in_at: null },
      { user_id: "demo-guard", email: "guard@example.com", role: null,
        created_at: iso(now - 2 * day), last_sign_in_at: null },
    ];
    this.changes = [
      { id: 3, ts: iso(now - 9 * day), actor_email: "khalid@example.com", action: "member.add",
        target_email: "ahmed@example.com", old_role: null, new_role: "member" },
      { id: 2, ts: iso(now - 20 * day), actor_email: "khalid@example.com", action: "password.reset",
        target_email: "sara@example.com", old_role: null, new_role: null },
      { id: 1, ts: iso(now - 21 * day), actor_email: "khalid@example.com", action: "member.add",
        target_email: "sara@example.com", old_role: null, new_role: "member" },
    ];
  }

  private self(): Account {
    const email = this.role === "admin" ? "khalid@example.com" : "sara@example.com";
    return this.accounts.find((a) => a.email === email)!;
  }
  private admin() {
    need(this.self().role === "admin", "not_admin");
  }
  private find(id: string): Account {
    const hit = this.accounts.find((a) => a.user_id === id);
    need(hit, "no_user");
    return hit;
  }
  private note(action: string, target: Account, before: string | null, after: string | null) {
    this.changes.unshift({ id: Date.now(), ts: iso(Date.now()), actor_email: this.self().email, action,
                           target_email: target.email, old_role: before, new_role: after });
  }

  async me(): Promise<Me> {
    const me = this.self();
    return { user_id: me.user_id, email: me.email, role: me.role };
  }
  async events(fromISO: string) {
    const who = this.self().role;
    return all().filter((r) => r.ts >= fromISO && visibleTo(who, r)).map((r) => ({ ...r }));
  }
  async count(category: string, fromISO: string, toISO: string) {
    const who = this.self().role;
    return all().filter((r) => r.category === category && r.ts >= fromISO && r.ts < toISO &&
                               visibleTo(who, r)).length;
  }
  async status(): Promise<DoorStatus> {
    return { id: "door", updated_at: iso(Date.now() - 20000), lock: "locked", alarm: "",
             camera: true, bot: true, info: { card: true, fingerprint: true } };
  }
  async sign() { return {}; }
  subscribe() {}
  unsubscribe() {}
  async signOut() {}
  async changePassword(password: string) {
    need(String(password).length >= MIN_PASSWORD, "weak_password");
    await later();
  }
  async members() {
    this.admin();
    const me = this.self();
    return later(this.accounts.map((a) => ({ ...a, is_me: a === me })));
  }
  async auditLog(limit: number) {
    this.admin();
    return this.changes.slice(0, limit);
  }
  async setRole(id: string, next: Role) {
    this.admin();
    need(next === "admin" || next === "member", "bad_role");
    const target = this.find(id);
    need(target !== this.self(), "self_change");
    if (target.role !== next) {
      this.note(target.role ? "member.role" : "member.add", target, target.role, next);
      target.role = next;
    }
    await later();
  }
  async createAccount(email: string, password: string, next: Role) {
    this.admin();
    email = String(email).trim().toLowerCase();
    need(EMAIL.test(email), "bad_email");
    need(String(password).length >= MIN_PASSWORD, "weak_password");
    need(next === "admin" || next === "member", "bad_role");
    need(!this.accounts.some((a) => a.email === email), "email_exists");
    const account: Account = { user_id: "demo-" + Date.now(), email, role: next,
                               created_at: iso(Date.now()), last_sign_in_at: null };
    this.accounts.push(account);
    this.note("member.add", account, null, next);
    return later({ ok: true });
  }
  async deleteAccount(id: string) {
    this.admin();
    const target = this.find(id);
    need(target !== this.self(), "self_change");
    if (target.role) this.note("member.remove", target, target.role, null);
    this.accounts.splice(this.accounts.indexOf(target), 1);
    return later({ ok: true });
  }
  async resetPassword(id: string, password: string) {
    this.admin();
    const target = this.find(id);
    need(String(password).length >= MIN_PASSWORD, "weak_password");
    this.note("password.reset", target, null, null);
    return later({ ok: true });
  }

  // ------------------------------------------------ chat (answered right here)
  async chatList(limit: number) {
    this.admin();
    return this.chat.slice(-limit).map((m) => ({ ...m }));
  }
  async chatSend(text: string) {
    this.admin();
    const body = String(text).trim();
    need(body.length >= 1 && body.length <= 1000, "bad_request");
    const asked = this.chatPut({ sender: "admin", body, texts: null, photo: "", intent: "",
                                 status: "pending", reply_to: null });
    setTimeout(() => this.chatPut({ ...asked, status: "working" }), 400);
    setTimeout(() => {
      const [intent, ar, en, photo] = demoReply(body);
      this.chatPut({ sender: "door", body: /[\u0600-\u06FF]/.test(body) ? ar : en, texts: { ar, en },
                     photo, intent, status: "done", reply_to: asked.id });
      this.chatPut({ ...asked, status: "done", intent });
    }, 1300);
    return { ...asked };
  }
  async chatClear() {
    this.admin();
    this.chat = [];
  }
  chatSubscribe(onChange: (m: ChatMessage) => void) {
    this.chatListeners.add(onChange);
    return () => { this.chatListeners.delete(onChange); };
  }
  private chatPut(m: Partial<ChatMessage> & Omit<ChatMessage, "id" | "created_at">): ChatMessage {
    const row: ChatMessage = { id: m.id ?? ++this.chatId, created_at: m.created_at ?? iso(Date.now()), ...m };
    const at = this.chat.findIndex((x) => x.id === row.id);
    if (at >= 0) this.chat[at] = row;
    else this.chat.push(row);
    for (const listener of this.chatListeners) listener({ ...row });
    return row;
  }
}

/** What the demo door says: [intent, Arabic, English, picture]. */
function demoReply(text: string): [string, string, string, string] {
  const t = text.toLowerCase();
  if (/افتح|فتح|open/.test(t)) {
    return ["open", "🚪 تم فتح الباب، ويقفل تلقائيًا بعد 5 ثوانٍ. (عرض تجريبي)",
            "🚪 Door opened. It locks again in 5 seconds. (demo)", ""];
  }
  if (/اقفل|قفل|lock/.test(t)) return ["lock", "🔒 الباب مقفل أصلًا.", "🔒 The door is already locked.", ""];
  if (/صور|camera|photo|picture|pic/.test(t)) {
    return ["photo", "📷 كاميرا الباب، الآن.", "📷 Door camera, just now.", picture("doorbell", Date.now())];
  }
  if (/رمز|كود|ضيف|code|guest/.test(t)) {
    const code = String(100000 + Math.floor(Math.random() * 900000));
    return ["tempcode", `🔑 رمز ضيف: ${code}\nصالح لمرة واحدة، لمدة دقيقة.`,
            `🔑 Guest code: ${code}\nGood for one entry, for one minute.`, ""];
  }
  if (/الحالة|حالة|status|state/.test(t)) {
    return ["status", "🔒 الباب مقفل.\n📷 الكاميرا تعمل · 👆 البصمة تعمل · 💳 قارئ الكروت يعمل\n✅ لا يوجد إنذار.",
            "🔒 The door is locked.\n📷 Camera on · 👆 Fingerprint on · 💳 Card reader on\n✅ No alarm.", ""];
  }
  if (/سجل|log|history/.test(t)) {
    return ["log", "📋 آخر الأحداث:\n• دخول بالوجه — Khalid\n• رنّ الجرس\n• رمز خاطئ",
            "📋 Latest events:\n• Entry by face — Khalid\n• Doorbell rang\n• Wrong code", ""];
  }
  return ["ai", "🤖 هذا عرض تجريبي. في النظام الحقيقي يفهم OpenAI الرسائل الحرة، مثل «جايني ضيف ابي رمز»، وينفّذها.",
          "🤖 This is a demo. On the real door OpenAI understands free text, like “a guest is coming, I need a code”, and acts on it.", ""];
}
