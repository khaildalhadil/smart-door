import { useRef, useState, type FormEvent } from "react";
import { errorText } from "../lib/errors";
import { tr, withLang } from "../lib/i18n";
import { Icon } from "./Icons";
import { LangButton } from "./LangButton";

export type GateMode = "setup" | "login" | "denied";

/** Before the dashboard: set-up steps, the sign-in form, or "no access". */
export function Gate({ mode, error, email, onLogin, onLogout, onDemo }: {
  mode: GateMode;
  error?: unknown;
  email?: string;
  onLogin: (email: string, password: string) => Promise<void>;
  onLogout: () => void;
  onDemo: (role: "admin" | "member") => void;
}) {
  const [busy, setBusy] = useState(false);
  // Read from the fields at submit time: password managers fill them without typing.
  const address = useRef<HTMLInputElement>(null);
  const password = useRef<HTMLInputElement>(null);
  const message = error === undefined ? "" : errorText(error);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await onLogin(address.current!.value.trim(), password.current!.value);
      if (password.current) password.current.value = "";
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="gate" className="gate">
      <section className="gate-card">
        <div className="gate-top">
          <div className="brand">
            <Icon name="lock" className="icon-lg" />
            <h1>{tr("القفل الذكي", "Smart Lock")}</h1>
          </div>
          <LangButton />
        </div>

        {mode === "setup" && (
          <div id="gate-setup">
            <p className="lead">{tr("الصفحة جاهزة، وباقي ربطها بقاعدة البيانات.",
                                    "The page is ready; it still has to be connected to the database.")}</p>
            <ol className="steps">
              {tr(
                <>
                  <li>أنشئ مشروعًا في <span dir="ltr">supabase.com</span> ونفّذ الملف <code dir="ltr">supabase/schema.sql</code> من SQL Editor.</li>
                  <li>أنشئ حسابك من <span dir="ltr">Authentication → Users → Add user</span>، ثم اجعله مديرًا (القسم 9 في آخر الملف).</li>
                  <li>انشر الدالة <code dir="ltr">door-accounts</code> لإضافة الحسابات من الصفحة (README).</li>
                  <li>ضع <code dir="ltr">Project URL</code> والمفتاح <code dir="ltr">publishable</code> في الملف <code dir="ltr">web/public/config.js</code>.</li>
                </>,
                <>
                  <li>Create a project at supabase.com and run <code>supabase/schema.sql</code> in its SQL Editor.</li>
                  <li>Create your account in Authentication → Users → Add user, then make it an admin (section 9 at the end of the file).</li>
                  <li>Deploy the <code>door-accounts</code> function, to add accounts from this page (README).</li>
                  <li>Put the <code>Project URL</code> and the <code>publishable</code> key in <code>web/public/config.js</code>.</li>
                </>,
              )}
            </ol>
            <div className="demo-buttons">
              <button id="demo-button" className="button primary" type="button" onClick={() => onDemo("admin")}>
                {tr("عرض تجريبي بصلاحية مدير", "Demo as an admin")}
              </button>
              <button id="demo-member" className="button" type="button" onClick={() => onDemo("member")}>
                {tr("عرض تجريبي بصلاحية مستخدم", "Demo as a member")}
              </button>
            </div>
          </div>
        )}

        {mode === "login" && (
          <form id="gate-login" onSubmit={submit}>
            <p className="lead">{tr("سجّل الدخول لعرض بيانات الباب.", "Sign in to see the door's data.")}</p>
            <label>{tr("البريد الإلكتروني", "Email")}
              <input id="login-email" type="email" autoComplete="username" required dir="ltr" ref={address} />
            </label>
            <label>{tr("كلمة المرور", "Password")}
              <input id="login-password" type="password" autoComplete="current-password" required dir="ltr"
                     ref={password} />
            </label>
            <p id="login-error" className="form-error" role="alert" hidden={!message}>{message}</p>
            <button className="button primary" type="submit" disabled={busy}>{tr("دخول", "Sign in")}</button>
            <p className="muted demo-link">
              {tr(<>أو شاهد <a href={withLang("?demo")}>عرضًا تجريبيًا</a> ببيانات وهمية.</>,
                  <>Or see a <a href={withLang("?demo")}>demo</a> with made-up data.</>)}
            </p>
          </form>
        )}

        {mode === "denied" && (
          <div id="gate-denied">
            <p id="denied-lead" className="lead" hidden={!!message}>
              {tr(<>الحساب <bdi id="denied-email" className="email">{email || ""}</bdi> مسجّل الدخول، لكن ليست له صلاحية على هذه اللوحة.</>,
                  <>The account <bdi id="denied-email" className="email">{email || ""}</bdi> is signed in, but has no access to this dashboard.</>)}
            </p>
            <p id="denied-hint" className="muted" hidden={!!message}>
              {tr("اطلب من مدير الباب أن يضيفك من قسم «الحسابات والصلاحيات».",
                  "Ask the door's admin to add you under “Accounts and roles”.")}
            </p>
            <p id="denied-error" className="form-error" role="alert" hidden={!message}>{message}</p>
            <button id="denied-logout" className="button" type="button" onClick={onLogout}>
              {tr("تسجيل الخروج", "Sign out")}
            </button>
          </div>
        )}
      </section>
    </main>
  );
}
