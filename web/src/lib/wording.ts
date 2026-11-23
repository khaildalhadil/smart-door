import type { DoorEvent, Role } from "./types";

export const CATEGORY: Record<string, string> = {
  entry: "دخول", denied: "مرفوض", doorbell: "جرس", alarm: "إنذار",
  remote: "واتساب", admin: "إدارة", system: "النظام",
};

export const CATEGORY_ICON: Record<string, string> = {
  entry: "door", denied: "ban", doorbell: "bell", alarm: "flame",
  remote: "chat", admin: "gear", system: "gear",
};

export const METHOD: Record<string, string> = {
  keypad: "رمز الباب", temp_code: "رمز مؤقت", face: "الوجه", fingerprint: "البصمة",
  card: "الكرت", whatsapp: "واتساب", doorbell: "عبر الجرس", menu: "لوحة التحكم",
  login: "PIN", fire: "حريق", smoke: "دخان", test: "تجربة", camera: "الكاميرا",
  relay: "الريلاي", other: "أخرى",
};

/** The daily chart shows three groups plus "other": three hues stay
 *  distinguishable in every pairing, for every viewer, in both themes. */
export const GROUPS = [
  { key: "bio", name: "وجه وبصمة", color: "var(--series-1)" },
  { key: "code", name: "رمز أو كرت", color: "var(--series-2)" },
  { key: "remote", name: "عن بُعد (واتساب)", color: "var(--series-3)" },
  { key: "other", name: "أخرى", color: "var(--series-other)" },
];

export const GROUP_OF: Record<string, string> = {
  face: "bio", fingerprint: "bio", keypad: "code", temp_code: "code", card: "code",
  whatsapp: "remote", doorbell: "remote",
};

const COMMAND: Record<string, string> = {
  open: "فتح الباب", lock: "قفل الباب", status: "الحالة", tempcode: "رمز مؤقت",
  photo: "صورة", log: "السجل", summary: "ملخص", help: "مساعدة",
  bell_open: "فتح للزائر", bell_deny: "رفض الزائر", keep_shut: "عدم الفتح",
};

const ADMIN: Record<string, string> = {
  "session.login": "تسجيل دخول على اللوحة", "passcode.changed": "تغيير رمز الباب",
  "user.add": "إضافة مستخدم", "user.delete": "حذف مستخدم",
  "user.enabled": "تفعيل أو تعطيل مستخدم", "user.pin": "تغيير PIN",
  "face.add": "تسجيل وجه", "face.delete": "حذف وجه",
  "fingerprint.add": "تسجيل بصمة", "fingerprint.enroll": "تسجيل بصمة",
  "fingerprint.delete": "حذف بصمة", "fingerprint.erase": "مسح بصمة من الحساس",
  "fingerprint.clear": "مسح كل البصمات", "card.add": "تسجيل كرت", "card.delete": "حذف كرت",
  "security.reset": "مسح الحظر", "app.exit": "إغلاق البرنامج",
};

const SYSTEM: Record<string, string> = {
  "app.start": "تشغيل النظام", "lock.shutdown": "إيقاف النظام",
  "fingerprint.offline": "حساس البصمة لا يرد", "fingerprint.online": "حساس البصمة رجع يعمل",
  "fingerprint.fault": "خطأ في حساس البصمة", "fingerprint.error": "خطأ في حساس البصمة",
  "card.offline": "قارئ الكروت لا يرد", "card.online": "قارئ الكروت رجع يعمل",
  "lock.io": "عطل في ريلاي القفل", "api.auth": "طلب مرفوض على واجهة البوت",
  "face.error": "خطأ في الكاميرا", "ui.exception": "خطأ في الواجهة",
  "api.exception": "خطأ في واجهة البوت", "hazard.exception": "خطأ في حساس الإنذار",
};

export const TABS: [string, string][] = [
  ["all", "الكل"], ["entry", "دخول"], ["denied", "مرفوض"], ["doorbell", "جرس"], ["alarm", "إنذار"],
];

export type Range = "today" | "7d" | "30d" | "90d";
export const RANGES: [Range, string][] = [
  ["today", "اليوم"], ["7d", "7 أيام"], ["30d", "30 يومًا"], ["90d", "90 يومًا"],
];
export const RANGE_DAYS: Record<Range, number> = { today: 1, "7d": 7, "30d": 30, "90d": 90 };

// --------------------------------------------------------------------- roles
export const ROLE_NAME: Record<Role, string> = { admin: "مدير", member: "مستخدم" };
export const ROLE_NOTE: Record<Role, string> = { admin: "كل الصلاحيات", member: "صلاحيات محدودة" };

/** [what, admin, member]: the table at the top of supabase/schema.sql, where it is enforced. */
export const PERMISSIONS: [string, boolean, boolean][] = [
  ["حالة الباب الآن", true, true],
  ["الأرقام والرسوم وتحليل الفترة", true, true],
  ["سجل الدخول والمحاولات المرفوضة والجرس والإنذارات", true, true],
  ["تغيير كلمة مرور حسابه", true, true],
  ["صور الكاميرا", true, false],
  ["أوامر واتساب وسجل الإدارة والنظام", true, false],
  ["إدارة الحسابات والصلاحيات وسجل تغييراتها", true, false],
];

/** Refusals a member sees. */
export const MEMBER_DENIED = ["keypad", "face", "fingerprint", "card"];

export const AUDIT_ICON: Record<string, string> = {
  "member.add": "user", "member.role": "shield", "member.remove": "trash", "password.reset": "key",
};

/** The "door members read events" policy, repeated for the demo only. */
export function visibleTo(role: Role | null, r: DoorEvent): boolean {
  if (role === "admin") return true;
  if (role !== "member") return false;
  return ["entry", "doorbell", "alarm"].includes(r.category) ||
    (r.category === "denied" && MEMBER_DENIED.includes(r.method));
}

/** One event in a few Arabic words. */
export function describe(r: DoorEvent): string {
  const how = METHOD[r.method] || r.method || "";
  const detail = r.detail || "";
  const disabled = /disabled/.test(detail);
  switch (r.category) {
    case "entry":
      return "دخول — " + (how || "غير محدد");
    case "denied":
      if (r.method === "whatsapp") return "أمر من رقم غير مصرّح";
      if (r.event === "keypad.lockout") return "قفل لوحة الأرقام بعد تكرار الرمز الخاطئ";
      if (r.event === "card.lockout") return "قفل قارئ الكروت بعد تكرار كروت غير مسجّلة";
      if (r.event === "login.lockout") return "قفل تسجيل الدخول بعد تكرار PIN الخاطئ";
      if (r.method === "keypad") return "رمز خاطئ";
      if (r.method === "face") return disabled ? "وجه حسابه معطّل" : "وجه غير معروف";
      if (r.method === "fingerprint") return disabled ? "بصمة حسابها معطّل" : "بصمة غير مسجّلة";
      if (r.method === "card") return disabled ? "كرت حسابه معطّل" : "كرت غير مسجّل";
      if (r.method === "login") return "PIN خاطئ على اللوحة";
      return "محاولة مرفوضة";
    case "doorbell":
      if (r.event === "doorbell.ring") return /again/.test(detail) ? "رنّ الجرس مرة ثانية" : "رنّ الجرس";
      if (r.event === "doorbell.timeout") return "الجرس: لم يرد المالك";
      if (/opened/.test(detail)) return "الجرس: المالك فتح الباب";
      if (/declined/.test(detail)) return "الجرس: المالك رفض";
      return "الجرس";
    case "alarm": {
      const what = r.method === "smoke" ? "الدخان" : "الحريق";
      if (/alarm over/.test(detail)) return "انتهى إنذار " + what;
      if (r.event === "lock.unlock") return "فُتح الباب بسبب إنذار " + what;
      return "إنذار " + (r.method === "smoke" ? "دخان" : "حريق");
    }
    case "remote": {
      if (r.event === "tempcode.add") return "رمز مؤقت جديد";
      const word = detail.split(" ")[0];
      return "أمر واتساب: " + (COMMAND[word] || word);
    }
    case "admin":
      return ADMIN[r.event] || r.event;
    default:
      if (r.event === "face.camera") return /offline/.test(detail) ? "الكاميرا انفصلت" : "الكاميرا رجعت تعمل";
      return SYSTEM[r.event] || r.event;
  }
}
