/** Numbers, dates and durations in the page's language, with Latin digits. */
import { lang, tr } from "./i18n";

const NUM = new Intl.NumberFormat("en-US");

function formats(locale: string) {
  return {
    day: new Intl.DateTimeFormat(locale, { day: "numeric", month: "long" }),
    weekday: new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", month: "long" }),
    time: new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hour12: false }),
    full: new Intl.DateTimeFormat(locale, {
      weekday: "long", day: "numeric", month: "long", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }),
  };
}
const FORMATS = { ar: formats("ar-u-nu-latn"), en: formats("en-GB") };
const f = () => FORMATS[lang()];

export const fmtDay = (t: number | Date) => f().day.format(t);
export const fmtWeekday = (t: number | Date) => f().weekday.format(t);
export const fmtTime = (t: number | Date) => f().time.format(t);
export const fmtFull = (t: number | Date) => f().full.format(t);

export const n = (value: number): string => NUM.format(value);
export const sum = (list: number[]): number => list.reduce((a, x) => a + x, 0);

/** English singular or plural noun. */
export const plural = (value: number, one: string, many: string) => (value === 1 ? one : many);

/** A number and its noun: Arabic agreement (1, 2, 3-10, 11+), or English singular/plural. */
export function count(value: number, ar: [string, string, string, string], en: [string, string]): string {
  if (lang() === "en") return n(value) + " " + plural(value, en[0], en[1]);
  const [one, two, few, many] = ar;
  if (value === 1) return one;
  if (value === 2) return two;
  return n(value) + " " + (value >= 3 && value <= 10 ? few : many);
}

export function times(value: number): string {
  if (lang() === "en") return value === 1 ? "once" : value === 2 ? "twice" : n(value) + " times";
  return count(value, ["مرة واحدة", "مرتين", "مرات", "مرة"], ["time", "times"]);
}

const SECOND: [string, string, string, string] = ["ثانية", "ثانيتين", "ثوانٍ", "ثانية"];
const MINUTE: [string, string, string, string] = ["دقيقة", "دقيقتين", "دقائق", "دقيقة"];
const HOUR: [string, string, string, string] = ["ساعة", "ساعتين", "ساعات", "ساعة"];
const DAY: [string, string, string, string] = ["يوم", "يومين", "أيام", "يومًا"];

export function startOfDay(t: number | Date): Date {
  const d = new Date(t);
  d.setHours(0, 0, 0, 0);
  return d;
}

export const hourLabel = (h: number) => (h < 10 ? "0" : "") + h + ":00";

export function when(t: number): string {
  const day = startOfDay(t).getTime();
  const today = startOfDay(Date.now()).getTime();
  const clock = fmtTime(t);
  if (day === today) return tr("اليوم ", "Today ") + clock;
  if (day === today - 86400000) return tr("أمس ", "Yesterday ") + clock;
  return fmtDay(t) + " " + clock;
}

export function ago(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  const m = Math.round(s / 60);
  const h = Math.round(m / 60);
  const span = s < 60 ? count(s, SECOND, ["second", "seconds"])
    : m < 60 ? count(m, MINUTE, ["minute", "minutes"])
    : h < 48 ? count(h, HOUR, ["hour", "hours"])
    : count(Math.round(h / 24), DAY, ["day", "days"]);
  return tr("قبل " + span, span + " ago");
}

export function seconds(s: number): string {
  s = Math.round(s);
  if (s < 60) return count(s, SECOND, ["second", "seconds"]);
  return count(Math.round(s / 60), MINUTE, ["minute", "minutes"]);
}

/** Keeps an e-mail address in one piece inside Arabic text. */
export const isolate = (text: string) => "⁨" + text + "⁩";
