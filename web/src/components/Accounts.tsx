import { useId, useState, type ReactNode } from "react";
import { count, fmtFull, isolate, n, plural, when } from "../lib/format";
import { tr } from "../lib/i18n";
import { EMAIL, checkPassword, newPassword } from "../lib/passwords";
import type { Account, AuditRow, Me, Role, Source } from "../lib/types";
import { AUDIT_ICON, permissions, roleName, roleNote } from "../lib/wording";
import { Icon } from "./Icons";
import type { SheetJob } from "./Sheet";

// ------------------------------------------------------------ small pieces
export function RoleBadge({ role }: { role: Role | null }) {
  return (
    <span className={"role-chip " + (role || "none")}>
      <Icon name={role === "admin" ? "shield" : role ? "user" : "ban"} />
      {role ? roleName(role) : tr("بدون صلاحية", "No access")}
    </span>
  );
}

export function PermissionList({ role }: { role: Role }) {
  const column = role === "admin" ? 1 : 2;
  return (
    <ul className="perms">
      {permissions().map((p) => {
        const yes = p[column];
        return (
          <li key={p[0]} className={yes ? "yes" : "no"}>
            <Icon name={yes ? "check" : "close"} /><span>{p[0]}</span>
            <span className="sr">{yes ? tr(" (مسموح)", " (allowed)") : tr(" (غير مسموح)", " (not allowed)")}</span>
          </li>
        );
      })}
    </ul>
  );
}

function RolesTable() {
  const mark = (yes: boolean) => (
    <td className={yes ? "yes" : "no"}>
      <Icon name={yes ? "check" : "close"} /><span className="sr">{yes ? tr("نعم", "yes") : tr("لا", "no")}</span>
    </td>
  );
  return (
    <table className="roles">
      <thead>
        <tr><th><span className="sr">{tr("الإجراء", "What")}</span></th><th>{roleName("admin")}</th><th>{roleName("member")}</th></tr>
      </thead>
      <tbody>
        {permissions().map((p) => <tr key={p[0]}><th scope="row">{p[0]}</th>{mark(p[1])}{mark(p[2])}</tr>)}
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
                     <Icon name="refresh" />{tr("توليد", "Generate")}</button>}>
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
      <span><b>{roleName(role) + " · " + roleNote(role)}</b><small>{text}</small></span>
    </label>
  );
  return (
    <fieldset className="choices">
      <legend>{tr("الصلاحية", "Role")}</legend>
      {option("member", tr("حالة الباب والأرقام والسجل، بدون صور الكاميرا وسجل النظام وإدارة الحسابات.",
                           "The door's status, numbers and history; no camera pictures, system records or accounts."))}
      {option("admin", tr("كل شيء، ومنه صور الكاميرا وسجل النظام وإدارة الحسابات.",
                          "Everything, including camera pictures, system records and the accounts."))}
    </fieldset>
  );
}

// -------------------------------------------------------------- the forms
const Email = ({ email }: { email: string }) => <bdi className="email">{email}</bdi>;
const handOver = () => tr("انسخها وأعطها لصاحب الحساب؛ يستطيع تغييرها بعد الدخول من «حسابي».",
                          "Copy it and give it to the account's owner; they can change it under “My account”.");

export function addAccountJob(source: Source, reload: () => Promise<void>): SheetJob {
  const form = { email: "", password: newPassword(), role: "member" as Role };
  return {
    title: tr("إضافة حساب", "Add an account"),
    body: (
      <>
        <PlainField label={tr("البريد الإلكتروني", "Email")} type="email" autoComplete="off"
                    onChange={(v) => { form.email = v; }} />
        <NewPasswordField label={tr("كلمة مرور مؤقتة", "Temporary password")} initial={form.password}
                          onChange={(v) => { form.password = v; }} hint={handOver()} />
        <RoleChoice initial="member" onChange={(r) => { form.role = r; }} />
      </>
    ),
    submit: tr("إضافة الحساب", "Add the account"),
    onSubmit: async () => {
      const address = form.email.trim().toLowerCase();
      if (!EMAIL.test(address)) throw new Error("bad_email");
      checkPassword(form.password);
      await source.createAccount(address, form.password, form.role);
      await reload();
      return tr("أُضيف " + isolate(address) + " بصلاحية «" + roleName(form.role) + "»",
                "Added " + isolate(address) + " as “" + roleName(form.role) + "”");
    },
  };
}

function roleJob(source: Source, reload: () => Promise<void>, account: Account, next: Role): SheetJob {
  const grant = !account.role;
  const was = roleName(account.role), will = roleName(next);
  return {
    title: grant ? tr("منح صلاحية", "Give access") : tr("تغيير الصلاحية", "Change the role"),
    body: (
      <>
        <p>
          {grant
            ? tr(<>منح <Email email={account.email} />{" صلاحية «" + will + "»؟"}</>,
                 <>Give <Email email={account.email} />{" the role “" + will + "”?"}</>)
            : tr(<>تغيير صلاحية <Email email={account.email} />{" من «" + was + "» إلى «" + will + "»؟"}</>,
                 <>Change <Email email={account.email} />{" from “" + was + "” to “" + will + "”?"}</>)}
        </p>
        <p className="muted">{tr("بهذه الصلاحية:", "With this role:")}</p>
        <PermissionList role={next} />
      </>
    ),
    submit: tr("تأكيد", "Confirm"),
    onSubmit: async () => {
      await source.setRole(account.user_id, next);
      await reload();
      return tr("صلاحية " + isolate(account.email) + " الآن: " + will, isolate(account.email) + " is now: " + will);
    },
  };
}

function passwordJob(source: Source, reload: () => Promise<void>, account: Account): SheetJob {
  const form = { password: newPassword() };
  return {
    title: tr("كلمة مرور جديدة", "New password"),
    body: (
      <>
        <p>{tr("للحساب ", "For ")}<Email email={account.email} /></p>
        <NewPasswordField label={tr("كلمة المرور الجديدة", "New password")} initial={form.password}
                          onChange={(v) => { form.password = v; }} hint={handOver()} />
      </>
    ),
    submit: tr("حفظ كلمة المرور", "Save the password"),
    onSubmit: async () => {
      checkPassword(form.password);
      await source.resetPassword(account.user_id, form.password);
      void reload();
      return tr("تغيّرت كلمة مرور " + isolate(account.email), "New password set for " + isolate(account.email));
    },
  };
}

function deleteJob(source: Source, reload: () => Promise<void>, account: Account): SheetJob {
  return {
    title: tr("حذف حساب", "Delete an account"),
    body: (
      <>
        <p>{tr(<>حذف الحساب <Email email={account.email} /> نهائيًا؟</>,
               <>Delete the account <Email email={account.email} /> for good?</>)}</p>
        <p className="muted">{tr("لن يستطيع الدخول إلى هذه اللوحة بعد الآن. سجل الباب وصوره لا تتأثر.",
                                 "They will no longer be able to sign in here. The door's history and pictures stay.")}</p>
      </>
    ),
    submit: tr("حذف الحساب", "Delete the account"),
    danger: true,
    onSubmit: async () => {
      await source.deleteAccount(account.user_id);
      await reload();
      return tr("حُذف الحساب " + isolate(account.email), "Deleted " + isolate(account.email));
    },
  };
}

export function myAccountJob(source: Source, me: Me | null, role: Role): SheetJob {
  const form = { fresh: "", again: "" };
  return {
    title: tr("حسابي", "My account"),
    body: (
      <>
        <div className="me"><Email email={me?.email || ""} /><RoleBadge role={role} /></div>
        <p className="muted">{tr("ما تستطيع فعله بهذه الصلاحية:", "What you can do with this role:")}</p>
        <PermissionList role={role} />
        <h3 className="subhead">{tr("تغيير كلمة المرور", "Change the password")}</h3>
        <PlainField label={tr("كلمة المرور الجديدة", "New password")} type="password" autoComplete="new-password"
                    hint={tr("8 أحرف على الأقل.", "At least 8 characters.")} onChange={(v) => { form.fresh = v; }} />
        <PlainField label={tr("أعد كتابتها", "Type it again")} type="password" autoComplete="new-password"
                    onChange={(v) => { form.again = v; }} />
      </>
    ),
    submit: tr("حفظ كلمة المرور", "Save the password"),
    onSubmit: async () => {
      checkPassword(form.fresh);
      if (form.fresh !== form.again) throw new Error("mismatch");
      await source.changePassword(form.fresh);
      return tr("تغيّرت كلمة مرورك", "Your password was changed");
    },
  };
}

// --------------------------------------------------------------- the card
function auditLine(a: AuditRow): ReactNode {
  const who = <bdi>{a.actor_email || "—"}</bdi>;
  const whom = <bdi>{a.target_email || tr("حساب محذوف", "a deleted account")}</bdi>;
  const was = roleName(a.old_role), now = roleName(a.new_role);
  switch (a.action) {
    case "member.add":
      return tr(<>{who} منح {whom}{" صلاحية «" + now + "»"}</>, <>{who} gave {whom}{" the role “" + now + "”"}</>);
    case "member.role":
      return tr(<>{who} غيّر صلاحية {whom}{" من «" + was + "» إلى «" + now + "»"}</>,
                <>{who} changed {whom}{" from “" + was + "” to “" + now + "”"}</>);
    case "member.remove":
      return tr(<>{who} حذف حساب {whom}{" (كان «" + was + "»)"}</>, <>{who} deleted {whom}{" (was “" + was + "”)"}</>);
    case "password.reset":
      return tr(<>{who} وضع كلمة مرور جديدة لـ {whom}</>, <>{who} set a new password for {whom}</>);
    default:
      return <>{who}{" " + a.action + " "}{whom}</>;
  }
}

export function AccountsCard({ source, accounts, audit, error, reload, openSheet }: {
  source: Source; accounts: Account[] | null; audit: AuditRow[] | null; error: string;
  reload: () => Promise<void>; openSheet: (job: SheetJob) => void;
}) {
  let summary = tr("من يدخل هذه اللوحة، وبأي صلاحية", "Who can sign in here, and with which role");
  if (accounts) {
    const admins = accounts.filter((a) => a.role === "admin").length;
    const members = accounts.filter((a) => a.role === "member").length;
    const none = accounts.length - admins - members;
    summary = count(accounts.length, ["حساب واحد", "حسابان", "حسابات", "حسابًا"], ["account", "accounts"]) + ": " +
      tr("مدير " + n(admins) + " · مستخدم " + n(members) + (none ? " · بدون صلاحية " + n(none) : ""),
         n(admins) + " " + plural(admins, "admin", "admins") + " · " + n(members) + " " +
         plural(members, "member", "members") + (none ? " · " + n(none) + " without access" : ""));
  }
  return (
    <section className="card" id="card-accounts">
      <div className="section-head">
        <div>
          <h2><Icon name="shield" />{tr("الحسابات والصلاحيات", "Accounts and roles")}</h2>
          <p className="muted" id="accounts-count">{summary}</p>
        </div>
        <button id="account-add" className="button primary" type="button"
                onClick={() => openSheet(addAccountJob(source, reload))}>
          <Icon name="plus" />{tr("إضافة حساب", "Add an account")}
        </button>
      </div>
      <p id="accounts-error" className="banner error" role="alert" hidden={!error}>{error}</p>
      <div className="table-wrap">
        <table className="log accounts">
          <thead>
            <tr>
              <th>{tr("الحساب", "Account")}</th><th>{tr("الصلاحية", "Role")}</th>
              <th className="who">{tr("آخر دخول", "Last sign-in")}</th>
              <th><span className="sr">{tr("إجراءات", "Actions")}</span></th>
            </tr>
          </thead>
          <tbody id="accounts-body">
            {!accounts && <tr><td colSpan={4} className="empty">{tr("جارٍ التحميل…", "Loading…")}</td></tr>}
            {accounts?.map((a) => (
              <tr key={a.user_id}>
                <td><Email email={a.email} />{a.is_me && <span className="you">{tr("أنت", "you")}</span>}</td>
                <td>
                  {a.is_me ? <RoleBadge role={a.role} /> : (
                    <select className="role-select" aria-label={tr("صلاحية ", "Role of ") + a.email} value={a.role ?? ""}
                            onChange={(e) => openSheet(roleJob(source, reload, a, e.target.value as Role))}>
                      {!a.role && <option value="" disabled>{tr("بدون صلاحية", "No access")}</option>}
                      <option value="member">{roleName("member")}</option>
                      <option value="admin">{roleName("admin")}</option>
                    </select>
                  )}
                </td>
                <td className="when who">
                  {a.last_sign_in_at ? when(Date.parse(a.last_sign_in_at))
                                     : <span className="muted">{tr("لم يدخل بعد", "Not yet")}</span>}
                </td>
                <td>
                  {a.is_me ? <span className="muted">{tr("من «حسابي»", "Under “My account”")}</span> : (
                    <span className="row-actions">
                      <button className="link" type="button" onClick={() => openSheet(passwordJob(source, reload, a))}>
                        <Icon name="key" />{tr("كلمة مرور", "Password")}
                      </button>
                      <button className="link danger" type="button" onClick={() => openSheet(deleteJob(source, reload, a))}>
                        <Icon name="trash" />{tr("حذف", "Delete")}
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
        <summary><Icon name="table" />{tr("ماذا تستطيع كل صلاحية؟", "What can each role do?")}</summary>
        <div id="roles-table"><RolesTable /></div>
      </details>
      <h3 className="subhead">{tr("آخر التغييرات على الحسابات", "Recent changes to the accounts")}</h3>
      <ul id="audit" className="audit">
        {audit && !audit.length && <li className="empty">{tr("لا توجد تغييرات بعد", "No changes yet")}</li>}
        {audit?.map((a) => {
          const t = Date.parse(a.ts);
          return (
            <li key={a.id}>
              <Icon name={AUDIT_ICON[a.action] || "gear"} />
              <span>{auditLine(a)}</span>
              <time className="muted" dateTime={a.ts} title={fmtFull(t)}>{when(t)}</time>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
