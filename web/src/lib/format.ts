/** Numbers, dates and durations in Arabic, with Latin digits. */
const LOCALE = "ar-u-nu-latn";
const NUM = new Intl.NumberFormat("en-US");
export const F_DAY = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "long" });
export const F_WEEKDAY = new Intl.DateTimeFormat(LOCALE, { weekday: "long", day: "numeric", month: "long" });
export const F_TIME = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit", hour12: false });
export const F_FULL = new Intl.DateTimeFormat(LOCALE, {
  weekday: "long", day: "numeric", month: "long", year: "numeric",
  hour: "2-digit", minute: "2-digit", hour12: false,
});

export const n = (value: number): string => NUM.format(value);
export const sum = (list: number[]): number => list.reduce((a, x) => a + x, 0);

/** Arabic number agreement: 1, 2, 3-10, 11+. */
export function count(value: number, one: string, two: string, few: string, many: string): string {
  if (value === 1) return one;
  if (value === 2) return two;
  return n(value) + " " + (value >= 3 && value <= 10 ? few : many);
}
export const times = (value: number) => count(value, "مرة واحدة", "مرتين", "مرات", "مرة");

export function startOfDay(t: number | Date): Date {
  const d = new Date(t);
  d.setHours(0, 0, 0, 0);
  return d;
}

export const hourLabel = (h: number) => (h < 10 ? "0" : "") + h + ":00";

export function when(t: number): string {
  const day = startOfDay(t).getTime();
  const today = startOfDay(Date.now()).getTime();
  const clock = F_TIME.format(t);
  if (day === today) return "اليوم " + clock;
  if (day === today - 86400000) return "أمس " + clock;
  return F_DAY.format(t) + " " + clock;
}

export function ago(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return "قبل " + count(s, "ثانية", "ثانيتين", "ثوانٍ", "ثانية");
  const m = Math.round(s / 60);
  if (m < 60) return "قبل " + count(m, "دقيقة", "دقيقتين", "دقائق", "دقيقة");
  const h = Math.round(m / 60);
  if (h < 48) return "قبل " + count(h, "ساعة", "ساعتين", "ساعات", "ساعة");
  return "قبل " + count(Math.round(h / 24), "يوم", "يومين", "أيام", "يومًا");
}

export function seconds(s: number): string {
  s = Math.round(s);
  if (s < 60) return count(s, "ثانية", "ثانيتين", "ثوانٍ", "ثانية");
  return count(Math.round(s / 60), "دقيقة", "دقيقتين", "دقائق", "دقيقة");
}

/** Keeps an e-mail address in one piece inside Arabic text. */
export const isolate = (text: string) => "⁨" + text + "⁩";
