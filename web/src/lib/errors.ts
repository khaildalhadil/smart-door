import { tr } from "./i18n";

/** Error codes from the database functions, the door-accounts function and
 *  Supabase Auth, in words: [Arabic, English]. */
const ERRORS: Record<string, [string, string]> = {
  not_admin: ["هذا الإجراء للمدير فقط.", "Only an admin can do this."],
  self_change: ["لا يمكنك تغيير صلاحية حسابك أو حذفه بنفسك.",
                "You can't change your own role or delete your own account."],
  bad_role: ["صلاحية غير معروفة.", "Unknown role."],
  no_user: ["الحساب غير موجود (ربما حُذف).", "The account doesn't exist (it may have been deleted)."],
  email_exists: ["يوجد حساب بهذا البريد. يمكنك منحه صلاحية من القائمة.",
                 "An account with this email already exists. You can give it a role from the list."],
  weak_password: ["كلمة المرور ضعيفة: 8 أحرف على الأقل، ويُفضّل حروف وأرقام.",
                  "Weak password: at least 8 characters, ideally letters and digits."],
  long_password: ["كلمة المرور طويلة جدًا.", "The password is too long."],
  same_password: ["كلمة المرور الجديدة هي نفس الحالية.", "The new password is the same as the current one."],
  mismatch: ["كلمتا المرور غير متطابقتين.", "The two passwords don't match."],
  bad_email: ["البريد الإلكتروني غير صحيح.", "The email address isn't valid."],
  delete_failed: ["سُحبت صلاحية الحساب، لكن تعذّر حذفه نهائيًا. حاول مرة أخرى.",
                  "The account lost its access, but deleting it failed. Try again."],
  auth_error: ["خدمة الحسابات في Supabase لم ترد. حاول بعد قليل.",
               "Supabase's account service didn't answer. Try again shortly."],
  server_error: ["خطأ في الخادم. حاول بعد قليل.", "Server error. Try again shortly."],
  no_session: ["انتهت الجلسة. سجّل الدخول من جديد.", "Your session has ended. Sign in again."],
  bad_request: ["طلب غير صحيح.", "Invalid request."],
  function_missing: ["دالة الحسابات door-accounts غير منشورة في Supabase بعد (الخطوات في README).",
                     "The door-accounts function isn't deployed in Supabase yet (steps in the README)."],
};

interface ErrorLike { message?: string; error_description?: string; code?: string }

const known = (code: string) => tr(...ERRORS[code]);

/** Any error, in words, in the page's language. */
export function errorText(err: unknown): string {
  const e = (err ?? {}) as ErrorLike;
  const message = e.message || e.error_description || String(err);
  const code = e.code;
  if (Object.hasOwn(ERRORS, message)) return known(message);
  if (code && Object.hasOwn(ERRORS, code)) return known(code);
  if (/Failed to send a request to the Edge Function|Requested function was not found/i.test(message)) {
    return known("function_missing");
  }
  if (/Could not find the function|PGRST202/i.test(message)) {
    return tr("قاعدة البيانات غير مهيأة لهذه النسخة: نفّذ supabase/schema.sql في SQL Editor.",
              "The database isn't set up for this version: run supabase/schema.sql in the SQL Editor.");
  }
  if (/Invalid login credentials/i.test(message)) {
    return tr("البريد أو كلمة المرور غير صحيحة.", "Wrong email or password.");
  }
  if (/Email not confirmed/i.test(message)) {
    return tr("هذا البريد لم يُؤكَّد بعد. أكّده من لوحة Supabase.",
              "This email isn't confirmed yet. Confirm it in the Supabase dashboard.");
  }
  if (/Failed to fetch|NetworkError|Load failed/i.test(message)) {
    return tr("تعذّر الاتصال بقاعدة البيانات. تحقق من الإنترنت.",
              "Couldn't reach the database. Check the internet connection.");
  }
  if (/JWT|expired|401/i.test(message)) return known("no_session");
  if (/permission denied|42501/i.test(message)) {
    return tr("لا توجد صلاحية لهذا. تأكد أن supabase/schema.sql منفّذ كاملًا.",
              "No permission for this. Make sure supabase/schema.sql ran in full.");
  }
  if (/does not exist|42P01|PGRST205/i.test(message)) {
    return tr("الجداول غير موجودة. نفّذ supabase/schema.sql في SQL Editor.",
              "The tables don't exist. Run supabase/schema.sql in the SQL Editor.");
  }
  return tr("خطأ: ", "Error: ") + message;
}

/** The session is gone: back to the sign-in form. */
export const sessionLost = (err: unknown) =>
  /JWT|expired|401|no_session/i.test(String((err as ErrorLike)?.message ?? err));
