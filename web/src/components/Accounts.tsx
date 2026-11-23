import { useId, useState, type ReactNode } from "react";
import { count, F_FULL, isolate, n, when } from "../lib/format";
import { EMAIL, checkPassword, newPassword } from "../lib/passwords";
import type { Account, AuditRow, Me, Role, Source } from "../lib/types";
import { AUDIT_ICON, PERMISSIONS, ROLE_NAME, ROLE_NOTE } from "../lib/wording";
import { Icon } from "./Icons";
import type { SheetJob } from "./Sheet";

// ------------------------------------------------------------ small pieces
export function RoleBadge({ role }: { role: Role | null }) {
  return (
    <span className={"role-chip " + (role || "none")}>
      <Icon name={role === "admin" ? "shield" : role ? "user" : "ban"} />
      {role ? ROLE_NAME[role] : "بدون صلاحية"}
    </span>
  );
}

export function PermissionList({ role }: { role: Role }) {
  const column = role === "admin" ? 1 : 2;
  return (
    <ul className="perms">
      {PERMISSIONS.map((p) => {
        const yes = p[column];
        return (
          <li key={p[0]} className={yes ? "yes" : "no"}>
            <Icon name={yes ? "check" : "close"} /><span>{p[0]}</span>
            <span className="sr">{yes ? " (مسموح)" : " (غير مسموح)"}</span>
          </li>
        );
      })}
    </ul>
  );
}

function RolesTable() {
  const mark = (yes: boolean) => (
    <td className={yes ? "yes" : "no"}><Icon name={yes ? "check" : "close"} /><span className="sr">{yes ? "نعم" : "لا"}</span></td>
  );
  return (
    <table className="roles">
      <thead><tr><th><span className="sr">الإجراء</span></th><th>مدير</th><th>مستخدم</th></tr></thead>
      <tbody>
        {PERMISSIONS.map((p) => <tr key={p[0]}><th scope="row">{p[0]}</th>{mark(p[1])}{mark(p[2])}</tr>)}
      </tbody>
    </table>
  );
}

/** A label above an input, an optional button beside it and a hint below. */
function Field({ label, hint, beside, children }: {
  label: string; hint?: string; beside?: ReactNode; children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {beside ? <div className="input-row">{children(id)}{beside}</div> : children(id)}
      {hint && <small className="muted">{hint}</small>}
    </div>
  );
}

/** A readable password with a "make one" button: the admin hands it over. */
function NewPasswordField({ label, hint, initial, onChange }: {
  label: string; hint: string; initial: string; onChange: (value: string) => void;
}) {
  const [value, setValue] = useState(initial);
  const set = (v: string) => { setValue(v); onChange(v); };
  return (
    <Field label={label} hint={hint}
           beside={<button type="button" className="button" onClick={() => set(newPassword())}>
                     <Icon name="refresh" />توليد</button>}>
      {(id) => <input id={id} className="input" type="text" dir="ltr" autoComplete="off" spellCheck={false}
                      autoCapitalize="off" value={value} onChange={(e) => set(e.target.value)} />}
    </Field>
  );
}

function PlainField({ label, type, hint, autoComplete, onChange }: {
  label: string; type: string; hint?: string; autoComplete: string; onChange: (value: string) => void;
}) {
  return (
    <Field label={label} hint={hint}>
      {(id) => <input id={id} className="input" type={type} dir="ltr" autoComplete={autoComplete}
                      spellCheck={false} autoCapitalize="off" onChange={(e) => onChange(e.target.value)} />}
    </Field>
  );
}

function RoleChoice({ initial, onChange }: { initial: Role; onChange: (role: Role) => void }) {
  const name = useId();
  const option = (role: Role, text: string) => (
    <label className="choice">
      <input type="radio" name={name} value={role} defaultChecked={role === initial}
             onChange={() => onChange(role)} />
      <span><b>{ROLE_NAME[role] + " · " + ROLE_NOTE[role]}</b><small>{text}</small></span>
    </label>
  );
  return (
    <fieldset className="choices">
      <legend>الصلاحية</legend>
      {option("member", "حالة الباب والأرقام والسجل، بدون صور الكاميرا وسجل النظام وإدارة الحسابات.")}
      {option("admin", "كل شيء، ومنه صور الكاميرا وسجل النظام وإدارة الحسابات.")}
    </fieldset>
  );
}

// -------------------------------------------------------------- the forms
const Email = ({ email }: { email: string }) => <bdi className="email">{email}</bdi>;

export function addAccountJob(source: Source, reload: () => Promise<void>): SheetJob {
  const form = { email: "", password: newPassword(), role: "member" as Role };
  return {
    title: "إضافة حساب",
    body: (
      <>
        <PlainField label="البريد الإلكتروني" type="email" autoComplete="off" onChange={(v) => { form.email = v; }} />
        <NewPasswordField label="كلمة مرور مؤقتة" initial={form.password} onChange={(v) => { form.password = v; }}
                          hint="انسخها وأعطها لصاحب الحساب؛ يستطيع تغييرها بعد الدخول من «حسابي»." />
        <RoleChoice initial="member" onChange={(r) => { form.role = r; }} />
      </>
    ),
    submit: "إضافة الحساب",
    onSubmit: async () => {
      const address = form.email.trim().toLowerCase();
      if (!EMAIL.test(address)) throw new Error("bad_email");
      checkPassword(form.password);
      await source.createAccount(address, form.password, form.role);
      await reload();
      return "أُضيف " + isolate(address) + " بصلاحية «" + ROLE_NAME[form.role] + "»";
    },
  };
}

function roleJob(source: Source, reload: () => Promise<void>, account: Account, next: Role): SheetJob {
  const grant = !account.role;
  return {
    title: grant ? "منح صلاحية" : "تغيير الصلاحية",
    body: (
      <>
        <p>
          {grant ? "منح " : "تغيير صلاحية "}<Email email={account.email} />
          {grant ? " صلاحية «" + ROLE_NAME[next] + "»؟"
                 : " من «" + ROLE_NAME[account.role!] + "» إلى «" + ROLE_NAME[next] + "»؟"}
        </p>
        <p className="muted">بهذه الصلاحية:</p>
        <PermissionList role={next} />
      </>
    ),
    submit: "تأكيد",
    onSubmit: async () => {
      await source.setRole(account.user_id, next);
      await reload();
      return "صلاحية " + isolate(account.email) + " الآن: " + ROLE_NAME[next];
    },
  };
}

function passwordJob(source: Source, reload: () => Promise<void>, account: Account): SheetJob {
  const form = { password: newPassword() };
  return {
    title: "كلمة مرور جديدة",
    body: (
      <>
        <p>للحساب <Email email={account.email} /></p>
        <NewPasswordField label="كلمة المرور الجديدة" initial={form.password} onChange={(v) => { form.password = v; }}
                          hint="انسخها وأعطها لصاحب الحساب؛ يستطيع تغييرها من «حسابي»." />
      </>
    ),
    submit: "حفظ كلمة المرور",
    onSubmit: async () => {
      checkPassword(form.password);
      await source.resetPassword(account.user_id, form.password);
      void reload();
      return "تغيّرت كلمة مرور " + isolate(account.email);
    },
  };
}

function deleteJob(source: Source, reload: () => Promise<void>, account: Account): SheetJob {
  return {
    title: "حذف حساب",
    body: (
      <>
        <p>حذف الحساب <Email email={account.email} /> نهائيًا؟</p>
        <p className="muted">لن يستطيع الدخول إلى هذه اللوحة بعد الآن. سجل الباب وصوره لا تتأثر.</p>
      </>
    ),
    submit: "حذف الحساب",
    danger: true,
    onSubmit: async () => {
      await source.deleteAccount(account.user_id);
      await reload();
      return "حُذف الحساب " + isolate(account.email);
    },
  };
}

export function myAccountJob(source: Source, me: Me | null, role: Role): SheetJob {
  const form = { fresh: "", again: "" };
  return {
    title: "حسابي",
    body: (
      <>
        <div className="me"><Email email={me?.email || ""} /><RoleBadge role={role} /></div>
        <p className="muted">ما تستطيع فعله بهذه الصلاحية:</p>
        <PermissionList role={role} />
        <h3 className="subhead">تغيير كلمة المرور</h3>
        <PlainField label="كلمة المرور الجديدة" type="password" autoComplete="new-password" hint="8 أحرف على الأقل."
                    onChange={(v) => { form.fresh = v; }} />
        <PlainField label="أعد كتابتها" type="password" autoComplete="new-password"
                    onChange={(v) => { form.again = v; }} />
      </>
    ),
    submit: "حفظ كلمة المرور",
    onSubmit: async () => {
      checkPassword(form.fresh);
      if (form.fresh !== form.again) throw new Error("mismatch");
      await source.changePassword(form.fresh);
      return "تغيّرت كلمة مرورك";
    },
  };
}

// --------------------------------------------------------------- the card
function auditLine(a: AuditRow): ReactNode {
  const who = <bdi>{a.actor_email || "—"}</bdi>;
  const whom = <bdi>{a.target_email || "حساب محذوف"}</bdi>;
  const was = ROLE_NAME[a.old_role as Role] || a.old_role || "";
  const now = ROLE_NAME[a.new_role as Role] || a.new_role || "";
  switch (a.action) {
    case "member.add": return <>{who} منح {whom}{" صلاحية «" + now + "»"}</>;
    case "member.role": return <>{who} غيّر صلاحية {whom}{" من «" + was + "» إلى «" + now + "»"}</>;
    case "member.remove": return <>{who} حذف حساب {whom}{" (كان «" + was + "»)"}</>;
    case "password.reset": return <>{who} وضع كلمة مرور جديدة لـ {whom}</>;
    default: return <>{who}{" " + a.action + " "}{whom}</>;
  }
}

export function AccountsCard({ source, accounts, audit, error, reload, openSheet }: {
  source: Source; accounts: Account[] | null; audit: AuditRow[] | null; error: string;
  reload: () => Promise<void>; openSheet: (job: SheetJob) => void;
}) {
  let summary = "من يدخل هذه اللوحة، وبأي صلاحية";
  if (accounts) {
    const admins = accounts.filter((a) => a.role === "admin").length;
    const members = accounts.filter((a) => a.role === "member").length;
    const none = accounts.length - admins - members;
    summary = count(accounts.length, "حساب واحد", "حسابان", "حسابات", "حسابًا") +
      ": مدير " + n(admins) + " · مستخدم " + n(members) + (none ? " · بدون صلاحية " + n(none) : "");
  }
  return (
    <section className="card" id="card-accounts">
      <div className="section-head">
        <div>
          <h2><Icon name="shield" />الحسابات والصلاحيات</h2>
          <p className="muted" id="accounts-count">{summary}</p>
        </div>
        <button id="account-add" className="button primary" type="button"
                onClick={() => openSheet(addAccountJob(source, reload))}>
          <Icon name="plus" />إضافة حساب
        </button>
      </div>
      <p id="accounts-error" className="banner error" role="alert" hidden={!error}>{error}</p>
      <div className="table-wrap">
        <table className="log accounts">
          <thead><tr><th>الحساب</th><th>الصلاحية</th><th className="who">آخر دخول</th><th><span className="sr">إجراءات</span></th></tr></thead>
          <tbody id="accounts-body">
            {!accounts && <tr><td colSpan={4} className="empty">جارٍ التحميل…</td></tr>}
            {accounts?.map((a) => (
              <tr key={a.user_id}>
                <td><Email email={a.email} />{a.is_me && <span className="you">أنت</span>}</td>
                <td>
                  {a.is_me ? <RoleBadge role={a.role} /> : (
                    <select className="role-select" aria-label={"صلاحية " + a.email} value={a.role ?? ""}
                            onChange={(e) => openSheet(roleJob(source, reload, a, e.target.value as Role))}>
                      {!a.role && <option value="" disabled>بدون صلاحية</option>}
                      <option value="member">{ROLE_NAME.member}</option>
                      <option value="admin">{ROLE_NAME.admin}</option>
                    </select>
                  )}
                </td>
                <td className="when who">
                  {a.last_sign_in_at ? when(Date.parse(a.last_sign_in_at)) : <span className="muted">لم يدخل بعد</span>}
                </td>
                <td>
                  {a.is_me ? <span className="muted">من «حسابي»</span> : (
                    <span className="row-actions">
                      <button className="link" type="button" onClick={() => openSheet(passwordJob(source, reload, a))}>
                        <Icon name="key" />كلمة مرور
                      </button>
                      <button className="link danger" type="button" onClick={() => openSheet(deleteJob(source, reload, a))}>
                        <Icon name="trash" />حذف
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="table-view">
        <summary><Icon name="table" />ماذا تستطيع كل صلاحية؟</summary>
        <div id="roles-table"><RolesTable /></div>
      </details>
      <h3 className="subhead">آخر التغييرات على الحسابات</h3>
      <ul id="audit" className="audit">
        {audit && !audit.length && <li className="empty">لا توجد تغييرات بعد</li>}
        {audit?.map((a) => {
          const t = Date.parse(a.ts);
          return (
            <li key={a.id}>
              <Icon name={AUDIT_ICON[a.action] || "gear"} />
              <span>{auditLine(a)}</span>
              <time className="muted" dateTime={a.ts} title={F_FULL.format(t)}>{when(t)}</time>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
