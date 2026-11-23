/** Error codes from the database functions, the door-accounts function and
 *  Supabase Auth, in words. */
const ERRORS: Record<string, string> = {
  not_admin: "هذا الإجراء للمدير فقط.",
  self_change: "لا يمكنك تغيير صلاحية حسابك أو حذفه بنفسك.",
  bad_role: "صلاحية غير معروفة.",
  no_user: "الحساب غير موجود (ربما حُذف).",
  email_exists: "يوجد حساب بهذا البريد. يمكنك منحه صلاحية من القائمة.",
  weak_password: "كلمة المرور ضعيفة: 8 أحرف على الأقل، ويُفضّل حروف وأرقام.",
  long_password: "كلمة المرور طويلة جدًا.",
  same_password: "كلمة المرور الجديدة هي نفس الحالية.",
  mismatch: "كلمتا المرور غير متطابقتين.",
  bad_email: "البريد الإلكتروني غير صحيح.",
  delete_failed: "سُحبت صلاحية الحساب، لكن تعذّر حذفه نهائيًا. حاول مرة أخرى.",
  auth_error: "خدمة الحسابات في Supabase لم ترد. حاول بعد قليل.",
  server_error: "خطأ في الخادم. حاول بعد قليل.",
  no_session: "انتهت الجلسة. سجّل الدخول من جديد.",
  bad_request: "طلب غير صحيح.",
  function_missing: "دالة الحسابات door-accounts غير منشورة في Supabase بعد (الخطوات في README).",
};

interface ErrorLike { message?: string; error_description?: string; code?: string }

export function errorText(err: unknown): string {
  const e = (err ?? {}) as ErrorLike;
  const message = e.message || e.error_description || String(err);
  const code = e.code;
  if (Object.hasOwn(ERRORS, message)) return ERRORS[message];
  if (code && Object.hasOwn(ERRORS, code)) return ERRORS[code];
  if (/Failed to send a request to the Edge Function|Requested function was not found/i.test(message)) {
    return ERRORS.function_missing;
  }
  if (/Could not find the function|PGRST202/i.test(message)) {
    return "قاعدة البيانات غير مهيأة لهذه النسخة: نفّذ supabase/schema.sql في SQL Editor.";
  }
  if (/Invalid login credentials/i.test(message)) return "البريد أو كلمة المرور غير صحيحة.";
  if (/Email not confirmed/i.test(message)) return "هذا البريد لم يُؤكَّد بعد. أكّده من لوحة Supabase.";
  if (/Failed to fetch|NetworkError|Load failed/i.test(message)) return "تعذّر الاتصال بقاعدة البيانات. تحقق من الإنترنت.";
  if (/JWT|expired|401/i.test(message)) return "انتهت الجلسة. سجّل الدخول من جديد.";
  if (/permission denied|42501/i.test(message)) return "لا توجد صلاحية لهذا. تأكد أن supabase/schema.sql منفّذ كاملًا.";
  if (/does not exist|42P01|PGRST205/i.test(message)) return "الجداول غير موجودة. نفّذ supabase/schema.sql في SQL Editor.";
  return "خطأ: " + message;
}

/** The session is gone: back to the sign-in form. */
export const sessionLost = (err: unknown) =>
  /JWT|expired|401|no_session/i.test(String((err as ErrorLike)?.message ?? err));
