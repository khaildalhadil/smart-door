import { useState } from "react";
import { LOG_STEP } from "../lib/config";
import { count, n, when } from "../lib/format";
import type { DoorEvent } from "../lib/types";
import { CATEGORY, METHOD, describe } from "../lib/wording";
import { Icon } from "./Icons";
import { Tabs, Tag } from "./Photos";

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
    if (!needle) return true;
    return [describe(r), r.person, METHOD[r.method], CATEGORY[r.category], r.event, r.detail]
      .join(" ").toLowerCase().includes(needle);
  });
  const pages = Math.max(1, Math.ceil(all.length / LOG_STEP));
  const at = Math.min(page, pages - 1);
  const shown = all.slice(at * LOG_STEP, (at + 1) * LOG_STEP);
  const withPhotos = rows.filter((r) => r.photo);

  return (
    <section className="card" id="card-log">
      <div className="section-head">
        <div>
          <h2>سجل الأحداث</h2>
          <p className="muted" id="log-count">{count(all.length, "حدث واحد", "حدثان", "أحداث", "حدثًا")}</p>
        </div>
        <div className="log-tools">
          <Tabs id="log-tabs" current={current} choose={(t) => { setTab(t); setPage(0); }}
                extra={admin ? [["other", "أخرى"]] : []} />
          <input id="log-search" type="search" placeholder="بحث: اسم، طريقة…" aria-label="بحث في السجل"
                 value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} />
        </div>
      </div>
      <div className="table-wrap">
        <table className="log">
          <thead>
            <tr>
              <th>الوقت</th><th>النوع</th><th>الوصف</th><th className="who">الشخص</th>
              {admin && <th id="log-photo-head">صورة</th>}
            </tr>
          </thead>
          <tbody id="log-body">
            {!shown.length && (
              <tr><td colSpan={admin ? 5 : 4} className="empty">لا توجد أحداث مطابقة</td></tr>
            )}
            {shown.map((r) => (
              <tr key={r.uid}>
                <td className="when">{when(r.t!)}</td>
                <td><Tag r={r} /></td>
                <td>{describe(r)}<small>{r.detail || ""}</small></td>
                <td className="who">{r.person || "—"}</td>
                {admin && (
                  <td>
                    {r.photo && (
                      <button className="link" type="button" onClick={() => onOpen(withPhotos, withPhotos.indexOf(r))}>
                        <Icon name="camera" />عرض
                      </button>
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
          الأحدث
        </button>
        <span id="log-page" className="muted">{"صفحة " + n(at + 1) + " من " + n(pages)}</span>
        <button id="log-next" className="button" type="button" disabled={at >= pages - 1}
                onClick={() => setPage(at + 1)}>
          الأقدم
        </button>
      </div>
    </section>
  );
}
