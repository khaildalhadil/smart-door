import { lang, tr, type Lang } from "./i18n";
import type { DoorEvent, Role } from "./types";

/** key -> [Arabic, English] */
type Words = Record<string, [string, string]>;

function word(words: Words, key: string, l: Lang = lang()): string {
  const both = words[key];
  return both ? both[l === "en" ? 1 : 0] : key;
}

const CATEGORY: Words = {
  entry: ["دخول", "Entry"], denied: ["مرفوض", "Refused"], doorbell: ["جرس", "Doorbell"],
  alarm: ["إنذار", "Alarm"], remote: ["واتساب", "WhatsApp"], admin: ["إدارة", "Admin"],
  system: ["النظام", "System"],
};
export const categoryName = (key: string, l?: Lang) => word(CATEGORY, key, l);

export const CATEGORY_ICON: Record<string, string> = {
  entry: "door", denied: "ban", doorbell: "bell", alarm: "flame",
  remote: "chat", admin: "gear", system: "gear",
};

/** Categories whose events normally come with a camera picture. */
export const PHOTO_CATEGORIES = ["entry", "denied", "doorbell", "alarm"];

const METHOD: Words = {
  keypad: ["رمز الباب", "Door code"], temp_code: ["رمز مؤقت", "Guest code"], face: ["الوجه", "Face"],
  fingerprint: ["البصمة", "Fingerprint"], card: ["الكرت", "Card"], whatsapp: ["واتساب", "WhatsApp"], web: ["الموقع", "Website"],
  doorbell: ["عبر الجرس", "Via doorbell"], menu: ["لوحة التحكم", "Control panel"], login: ["PIN", "PIN"],
  fire: ["حريق", "Fire"], smoke: ["دخان", "Smoke"], test: ["تجربة", "Test"], camera: ["الكاميرا", "Camera"],
  relay: ["الريلاي", "Relay"], other: ["أخرى", "Other"],
};
/** How the door was opened (or refused), in words; "" for no method. */
export const methodName = (key: string, l?: Lang) => word(METHOD, key, l);

/** The daily chart shows three groups plus "other": three hues stay
 *  distinguishable in every pairing, for every viewer, in both themes. */
export const groups = () => [
  { key: "bio", name: tr("وجه وبصمة", "Face & fingerprint"), color: "var(--series-1)" },
  { key: "code", name: tr("رمز أو كرت", "Code or card"), color: "var(--series-2)" },
  { key: "remote", name: tr("عن بُعد (واتساب والموقع)", "Remote (WhatsApp, website)"), color: "var(--series-3)" },
  { key: "other", name: tr("أخرى", "Other"), color: "var(--series-other)" },
];

export const GROUP_OF: Record<string, string> = {
  face: "bio", fingerprint: "bio", keypad: "code", temp_code: "code", card: "code",
  whatsapp: "remote", web: "remote", doorbell: "remote",
};

const COMMAND: Words = {
  open: ["فتح الباب", "open the door"], lock: ["قفل الباب", "lock the door"], status: ["الحالة", "status"],
  tempcode: ["رمز مؤقت", "guest code"], photo: ["صورة", "photo"], log: ["السجل", "log"],
  summary: ["ملخص", "summary"], help: ["مساعدة", "help"], bell_open: ["فتح للزائر", "open for the visitor"],
  bell_deny: ["رفض الزائر", "turn the visitor away"], keep_shut: ["عدم الفتح", "keep the door shut"],
};

const ADMIN: Words = {
  "session.login": ["تسجيل دخول على اللوحة", "Signed in on the panel"],
  "passcode.changed": ["تغيير رمز الباب", "Door code changed"],
  "user.add": ["إضافة مستخدم", "User added"], "user.delete": ["حذف مستخدم", "User deleted"],
  "user.enabled": ["تفعيل أو تعطيل مستخدم", "User enabled or disabled"], "user.pin": ["تغيير PIN", "PIN changed"],
  "face.add": ["تسجيل وجه", "Face enrolled"], "face.delete": ["حذف وجه", "Face deleted"],
  "fingerprint.add": ["تسجيل بصمة", "Fingerprint enrolled"],
  "fingerprint.enroll": ["تسجيل بصمة", "Fingerprint enrolled"],
  "fingerprint.delete": ["حذف بصمة", "Fingerprint deleted"],
  "fingerprint.erase": ["مسح بصمة من الحساس", "Fingerprint erased from the sensor"],
  "fingerprint.clear": ["مسح كل البصمات", "All fingerprints erased"],
  "card.add": ["تسجيل كرت", "Card enrolled"], "card.delete": ["حذف كرت", "Card deleted"],
  "security.reset": ["مسح الحظر", "Lockout cleared"], "app.exit": ["إغلاق البرنامج", "App closed"],
};

const SYSTEM: Words = {
  "app.start": ["تشغيل النظام", "System started"], "lock.shutdown": ["إيقاف النظام", "System stopped"],
  "fingerprint.offline": ["حساس البصمة لا يرد", "Fingerprint sensor not responding"],
  "fingerprint.online": ["حساس البصمة رجع يعمل", "Fingerprint sensor back"],
  "fingerprint.fault": ["خطأ في حساس البصمة", "Fingerprint sensor fault"],
  "fingerprint.error": ["خطأ في حساس البصمة", "Fingerprint sensor error"],
  "card.offline": ["قارئ الكروت لا يرد", "Card reader not responding"],
  "card.online": ["قارئ الكروت رجع يعمل", "Card reader back"],
  "lock.io": ["عطل في ريلاي القفل", "Lock relay fault"],
  "api.auth": ["طلب مرفوض على واجهة البوت", "Refused request on the bot API"],
  "face.error": ["خطأ في الكاميرا", "Camera error"], "ui.exception": ["خطأ في الواجهة", "Screen error"],
  "api.exception": ["خطأ في واجهة البوت", "Bot API error"],
  "hazard.exception": ["خطأ في حساس الإنذار", "Alarm sensor error"],
};

export const tabs = (): [string, string][] => [
  ["all", tr("الكل", "All")], ["entry", tr("دخول", "Entry")], ["denied", tr("مرفوض", "Refused")],
  ["doorbell", tr("جرس", "Doorbell")], ["alarm", tr("إنذار", "Alarm")],
];

export type Range = "today" | "7d" | "30d" | "90d";
export const ranges = (): [Range, string][] => [
  ["today", tr("اليوم", "Today")], ["7d", tr("7 أيام", "7 days")],
  ["30d", tr("30 يومًا", "30 days")], ["90d", tr("90 يومًا", "90 days")],
];
export const RANGE_DAYS: Record<Range, number> = { today: 1, "7d": 7, "30d": 30, "90d": 90 };

// --------------------------------------------------------------------- roles
/** "admin" / "member" in words; anything else as it is. */
export function roleName(role: string | null | undefined): string {
  if (role === "admin") return tr("مدير", "Admin");
  if (role === "member") return tr("مستخدم", "Member");
  return role || "";
}
export const roleNote = (role: Role) => (role === "admin" ? tr("كل الصلاحيات", "Full access")
                                                           : tr("صلاحيات محدودة", "Limited access"));

/** [what, admin, member]: the table at the top of supabase/schema.sql, where it is enforced. */
export const permissions = (): [string, boolean, boolean][] => [
  [tr("حالة الباب الآن", "The door's status now"), true, true],
  [tr("الأرقام والرسوم وتحليل الفترة", "Numbers, charts and the period analysis"), true, true],
  [tr("سجل الدخول والمحاولات المرفوضة والجرس والإنذارات", "Entries, refused attempts, doorbell and alarms"), true, true],
  [tr("تغيير كلمة مرور حسابه", "Changing their own password"), true, true],
  [tr("صور الكاميرا", "Camera pictures"), true, false],
  [tr("أوامر واتساب وسجل الإدارة والنظام", "WhatsApp commands, admin and system records"), true, false],
  [tr("إدارة الحسابات والصلاحيات وسجل تغييراتها", "Accounts, roles and their change log"), true, false],
  [tr("مراسلة الباب من الموقع (مثل واتساب)", "Chatting with the door from the website (like WhatsApp)"), true, false],
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

/** One event in a few words (the page's language unless another is asked for). */
export function describe(r: DoorEvent, l: Lang = lang()): string {
  const t = (ar: string, en: string) => (l === "en" ? en : ar);
  const how = r.method ? methodName(r.method, l) : "";
  const detail = r.detail || "";
  const disabled = /disabled/.test(detail);
  switch (r.category) {
    case "entry":
      return t("دخول — ", "Entry — ") + (how || t("غير محدد", "unknown"));
    case "denied":
      if (r.method === "whatsapp") return t("أمر من رقم غير مصرّح", "Command from an unauthorised number");
      if (r.event === "keypad.lockout") {
        return t("قفل لوحة الأرقام بعد تكرار الرمز الخاطئ", "Keypad locked after repeated wrong codes");
      }
      if (r.event === "card.lockout") {
        return t("قفل قارئ الكروت بعد تكرار كروت غير مسجّلة", "Card reader locked after repeated unknown cards");
      }
      if (r.event === "login.lockout") {
        return t("قفل تسجيل الدخول بعد تكرار PIN الخاطئ", "Panel sign-in locked after repeated wrong PINs");
      }
      if (r.method === "keypad") return t("رمز خاطئ", "Wrong code");
      if (r.method === "face") {
        return disabled ? t("وجه حسابه معطّل", "Face of a disabled account") : t("وجه غير معروف", "Unknown face");
      }
      if (r.method === "fingerprint") {
        return disabled ? t("بصمة حسابها معطّل", "Fingerprint of a disabled account")
                        : t("بصمة غير مسجّلة", "Unregistered fingerprint");
      }
      if (r.method === "card") {
        return disabled ? t("كرت حسابه معطّل", "Card of a disabled account") : t("كرت غير مسجّل", "Unregistered card");
      }
      if (r.method === "login") return t("PIN خاطئ على اللوحة", "Wrong PIN on the panel");
      return t("محاولة مرفوضة", "Refused attempt");
    case "doorbell":
      if (r.event === "doorbell.ring") {
        return /again/.test(detail) ? t("رنّ الجرس مرة ثانية", "Doorbell rang again") : t("رنّ الجرس", "Doorbell rang");
      }
      if (r.event === "doorbell.timeout") return t("الجرس: لم يرد المالك", "Doorbell: the owner didn't answer");
      if (/opened/.test(detail)) return t("الجرس: المالك فتح الباب", "Doorbell: the owner opened the door");
      if (/declined/.test(detail)) return t("الجرس: المالك رفض", "Doorbell: the owner turned them away");
      return t("الجرس", "Doorbell");
    case "alarm": {
      const smoke = r.method === "smoke";
      if (/alarm over/.test(detail)) {
        return t("انتهى إنذار " + (smoke ? "الدخان" : "الحريق"), (smoke ? "Smoke" : "Fire") + " alarm over");
      }
      if (r.event === "lock.unlock") {
        return t("فُتح الباب بسبب إنذار " + (smoke ? "الدخان" : "الحريق"),
                 "Door opened by the " + (smoke ? "smoke" : "fire") + " alarm");
      }
      return t("إنذار " + (smoke ? "دخان" : "حريق"), (smoke ? "Smoke" : "Fire") + " alarm");
    }
    case "remote": {
      if (r.event === "tempcode.add") return t("رمز مؤقت جديد", "New guest code");
      const first = detail.split(" ")[0];
      return (r.method === "web" ? t("أمر من الموقع: ", "Website command: ")
                                 : t("أمر واتساب: ", "WhatsApp command: ")) + word(COMMAND, first, l);
    }
    case "admin":
      return word(ADMIN, r.event, l);
    default:
      if (r.event === "face.camera") {
        return /offline/.test(detail) ? t("الكاميرا انفصلت", "Camera disconnected")
                                      : t("الكاميرا رجعت تعمل", "Camera back online");
      }
      return word(SYSTEM, r.event, l);
  }
}
