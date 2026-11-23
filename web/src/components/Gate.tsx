import { useRef, useState, type FormEvent } from "react";
import { Icon } from "./Icons";

export type GateMode = "setup" | "login" | "denied";

/** Before the dashboard: set-up steps, the sign-in form, or "no access". */
export function Gate({ mode, message, email, onLogin, onLogout, onDemo }: {
  mode: GateMode;
  message?: string;
  email?: string;
  onLogin: (email: string, password: string) => Promise<void>;
  onLogout: () => void;
  onDemo: (role: "admin" | "member") => void;
}) {
  const [busy, setBusy] = useState(false);
  // Read from the fields at submit time: password managers fill them without typing.
  const address = useRef<HTMLInputElement>(null);
  const password = useRef<HTMLInputElement>(null);

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
        <div className="brand">
          <Icon name="lock" className="icon-lg" />
          <h1>القفل الذكي</h1>
        </div>

        {mode === "setup" && (
          <div id="gate-setup">
            <p className="lead">الصفحة جاهزة، وباقي ربطها بقاعدة البيانات.</p>
            <ol className="steps">
              <li>أنشئ مشروعًا في <span dir="ltr">supabase.com</span> ونفّذ الملف <code dir="ltr">supabase/schema.sql</code> من SQL Editor.</li>
              <li>أنشئ حسابك من <span dir="ltr">Authentication → Users → Add user</span>، ثم اجعله مديرًا (القسم 9 في آخر الملف).</li>
              <li>انشر الدالة <code dir="ltr">door-accounts</code> لإضافة الحسابات من الصفحة (README).</li>
              <li>ضع <code dir="ltr">Project URL</code> والمفتاح <code dir="ltr">publishable</code> في الملف <code dir="ltr">web/public/config.js</code>.</li>
            </ol>
            <div className="demo-buttons">
              <button id="demo-button" className="button primary" type="button" onClick={() => onDemo("admin")}>
                عرض تجريبي بصلاحية مدير
              </button>
              <button id="demo-member" className="button" type="button" onClick={() => onDemo("member")}>
                عرض تجريبي بصلاحية مستخدم
              </button>
            </div>
          </div>
        )}

        {mode === "login" && (
          <form id="gate-login" onSubmit={submit}>
            <p className="lead">سجّل الدخول لعرض بيانات الباب.</p>
            <label>البريد الإلكتروني
              <input id="login-email" type="email" autoComplete="username" required dir="ltr" ref={address} />
            </label>
            <label>كلمة المرور
              <input id="login-password" type="password" autoComplete="current-password" required dir="ltr"
                     ref={password} />
            </label>
            <p id="login-error" className="form-error" role="alert" hidden={!message}>{message}</p>
            <button className="button primary" type="submit" disabled={busy}>دخول</button>
            <p className="muted demo-link">
              أو شاهد <a href="?demo">عرضًا تجريبيًا</a> ببيانات وهمية.
            </p>
          </form>
        )}

        {mode === "denied" && (
          <div id="gate-denied">
            <p id="denied-lead" className="lead" hidden={!!message}>
              الحساب <bdi id="denied-email" className="email">{email || ""}</bdi> مسجّل الدخول، لكن ليست له صلاحية على هذه اللوحة.
            </p>
            <p id="denied-hint" className="muted" hidden={!!message}>اطلب من مدير الباب أن يضيفك من قسم «الحسابات والصلاحيات».</p>
            <p id="denied-error" className="form-error" role="alert" hidden={!message}>{message}</p>
            <button id="denied-logout" className="button" type="button" onClick={onLogout}>تسجيل الخروج</button>
          </div>
        )}
      </section>
    </main>
  );
}
