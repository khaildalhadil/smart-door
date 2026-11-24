/** Arabic and English.
 *
 *    tr("عربي", "English")   the text (or element) for the language on screen
 *    ?lang=en / ?lang=ar      opens the page in that language
 *
 * The language lives in one module variable that everything reads while it
 * renders; App switches it and re-renders the page from the top.  The choice
 * is kept in this browser (localStorage "door-lang"); without one the page is
 * in Arabic.
 */
import { createContext } from "react";

export type Lang = "ar" | "en";

const KEY = "door-lang";

function startLang(): Lang {
  let wanted = new URLSearchParams(window.location.search).get("lang");
  if (!wanted) {
    try {
      wanted = localStorage.getItem(KEY);
    } catch {
      /* private window */
    }
  }
  return wanted === "en" ? "en" : "ar";
}

let current: Lang = startLang();

export const lang = (): Lang => current;

/** The text (or element) for the language on screen. */
export function tr<T>(ar: T, en: T): T {
  return current === "en" ? en : ar;
}

/** The document follows the language: direction, lang attribute, title. */
export function applyLang(): void {
  const root = document.documentElement;
  root.lang = current;
  root.dir = current === "en" ? "ltr" : "rtl";
  document.title = tr("القفل الذكي · لوحة المتابعة", "Smart Lock · Dashboard");
}

/** Switch the language; App re-renders the page right after. */
export function setLang(next: Lang): void {
  current = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* private window: the choice lasts until the page is closed */
  }
  // A ?lang= in the address would bring the old language back on reload.
  const url = new URL(window.location.href);
  if (url.searchParams.has("lang")) {
    url.searchParams.set("lang", next);
    history.replaceState(history.state, "", url);
  }
  applyLang();
}

/** A link that keeps the English page English, even where the browser keeps nothing. */
export function withLang(query: string): string {
  return current === "en" ? query + "&lang=en" : query;
}

export const LangContext = createContext<{ lang: Lang; switchTo: (next: Lang) => void }>({
  lang: "ar",
  switchTo: () => {},
});
