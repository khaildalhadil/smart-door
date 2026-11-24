import { useState } from "react";
import { LOG_STEP } from "../lib/config";
import { count, n, when } from "../lib/format";
import { tr } from "../lib/i18n";
import type { DoorEvent } from "../lib/types";
import { PHOTO_CATEGORIES, categoryName, describe, methodName } from "../lib/wording";
import { Icon } from "./Icons";
import { Tabs, Tag } from "./Photos";

/** Words to search in: both languages, so "face" finds «الوجه» and back. */
function searchable(r: DoorEvent): string {
  return [describe(r, "ar"), describe(r, "en"), r.person, methodName(r.method, "ar"), methodName(r.method, "en"),
          categoryName(r.category, "ar"), categoryName(r.category, "en"), r.event, r.detail].join(" ").toLowerCase();
}

/** Every event of the period: tabs, a search box and pages of 25. */
export function EventLog({ rows, admin, onOpen }: {
  rows: DoorEvent[]; admin: boolean; onOpen: (list: DoorEvent[], at: number) => void;
}) {
  const [tab, setTab] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const current = !admin && tab === "other" ? "all" : tab;
  const needle = search.trim().toLowerCase();
  const all = rows.filter((r) => {
    if (current === "other") {
      if (!["remote", "admin", "system"].includes(r.category)) return false;
    } else if (current !== "all" && r.category !== current) {
      return false;
    }
    return !needle || searchable(r).includes(needle);
  });
  const pages = Math.max(1, Math.ceil(all.length / LOG_STEP));
  const at = Math.min(page, pages - 1);
  const shown = all.slice(at * LOG_STEP, (at + 1) * LOG_STEP);
  const withPhotos = rows.filter((r) => r.photo);

  return (
    <section className="card" id="card-log">
      <div className="section-head">
        <div>
          <h2>{tr("سجل الأحداث", "Event log")}</h2>
          <p className="muted" id="log-count">{count(all.length, ["حدث واحد", "حدثان", "أحداث", "حدثًا"], ["event", "events"])}</p>
        </div>
        <div className="log-tools">
          <Tabs id="log-tabs" current={current} choose={(t) => { setTab(t); setPage(0); }}
                extra={admin ? [["other", tr("أخرى", "Other")]] : []} />
          <input id="log-search" type="search" placeholder={tr("بحث: اسم، طريقة…", "Search: name, method…")}
                 aria-label={tr("بحث في السجل", "Search the log")}
                 value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} />
        </div>
      </div>
      <div className="table-wrap">
        <table className="log">
          <thead>
            <tr>
              <th>{tr("الوقت", "Time")}</th><th>{tr("النوع", "Type")}</th><th>{tr("الوصف", "What happened")}</th>
              <th className="who">{tr("الشخص", "Person")}</th>
              {admin && <th id="log-photo-head">{tr("صورة", "Picture")}</th>}
            </tr>
          </thead>
          <tbody id="log-body">
            {!shown.length && (
              <tr><td colSpan={admin ? 5 : 4} className="empty">{tr("لا توجد أحداث مطابقة", "No matching events")}</td></tr>
            )}
            {shown.map((r) => (
              <tr key={r.uid}>
                <td className="when">{when(r.t!)}</td>
                <td><Tag r={r} /></td>
                <td>{describe(r)}<small>{r.detail || ""}</small></td>
                <td className="who">{r.person || "—"}</td>
                {admin && (
                  <td>
                    {r.photo ? (
                      <button className="link" type="button" onClick={() => onOpen(withPhotos, withPhotos.indexOf(r))}>
                        <Icon name="camera" />{tr("عرض", "View")}
                      </button>
                    ) : PHOTO_CATEGORIES.includes(r.category) && (
                      // No picture (the camera was off, or gave only black frames): say so.
                      <span className="no-photo"><Icon name="nophoto" />{tr("بدون صورة", "No picture")}</span>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <button id="log-prev" className="button" type="button" disabled={at <= 0} onClick={() => setPage(at - 1)}>
          {tr("الأحدث", "Newer")}
        </button>
        <span id="log-page" className="muted">{tr("صفحة " + n(at + 1) + " من " + n(pages), "Page " + n(at + 1) + " of " + n(pages))}</span>
        <button id="log-next" className="button" type="button" disabled={at >= pages - 1}
                onClick={() => setPage(at + 1)}>
          {tr("الأقدم", "Older")}
        </button>
      </div>
    </section>
  );
}
