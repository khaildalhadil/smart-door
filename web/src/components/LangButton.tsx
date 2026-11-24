import { useContext } from "react";
import { LangContext, type Lang } from "../lib/i18n";
import { Icon } from "./Icons";

/** "English" on the Arabic page, "عربي" on the English one. */
export function LangButton() {
  const { lang, switchTo } = useContext(LangContext);
  const other: Lang = lang === "en" ? "ar" : "en";
  return (
    <button id="lang" className="button ghost lang-button" type="button" lang={other}
            title={other === "en" ? "Switch to English" : "التبديل إلى العربية"}
            onClick={() => switchTo(other)}>
      <Icon name="globe" /><span>{other === "en" ? "English" : "عربي"}</span>
    </button>
  );
}
