import type { ReactNode } from "react";
import { ONLINE_WITHIN } from "../lib/config";
import { ago, hourLabel, n, seconds, sum, times, when } from "../lib/format";
import { tally } from "../lib/stats";
import type { DoorEvent, DoorStatus } from "../lib/types";
import { METHOD, RANGE_DAYS, type Range } from "../lib/wording";
import { Icon } from "./Icons";

// ------------------------------------------------------------- the door now
function Chip({ children }: { children: ReactNode }) {
  return <span className="chip">{children}</span>;
}

function Part({ icon, label, ok, yes, no, bad = "status-critical" }: {
  icon: string; label: string; ok: boolean; yes: string; no: string; bad?: string;
}) {
  return <Chip><Icon name={icon} className={ok ? "" : bad} />{label}<b>{ok ? yes : no}</b></Chip>;
}

export function Device({ status, now }: { status: DoorStatus | null; now: number }) {
  if (!status) {
    return (
      <section id="device" className="device" aria-label="حالة الجهاز">
        <Chip><i className="dot" />لم يصل أي تحديث من جهاز الباب بعد</Chip>
      </section>
    );
  }
  const age = now - Date.parse(status.updated_at);
  const online = age < ONLINE_WITHIN;
  const info = status.info || {};
  const reader = (key: string) => (typeof info[key] === "boolean" ? (info[key] as boolean) : null);
  const card = reader("card"), finger = reader("fingerprint");
  return (
    <section id="device" className="device" aria-label="حالة الجهاز">
      <Chip>
        <i className={"dot " + (online ? "good" : "critical")} />
        <b>{online ? "الجهاز متصل" : "الجهاز غير متصل"}</b>
        {(online ? "آخر تحديث " : "آخر ظهور ") + ago(age)}
      </Chip>
      {online && (
        // An old snapshot would only mislead: the rest only while the door reports in.
        <>
          {status.alarm && (
            <Chip>
              <Icon name="flame" className="status-critical" />
              <b>{"إنذار " + status.alarm.split("+").map((k) => METHOD[k] || k).join(" و") + " الآن"}</b>
            </Chip>
          )}
          <Chip><Icon name="lock" />القفل<b>{status.lock === "unlocked" ? "مفتوح" : "مقفل"}</b></Chip>
          {status.camera !== null && status.camera !== undefined && (
            <Part icon="camera" label="الكاميرا" ok={status.camera} yes="تعمل" no="متوقفة" />
          )}
          {status.bot !== null && status.bot !== undefined && (
            <Part icon="chat" label="بوت واتساب" ok={status.bot} yes="متصل" no="غير متصل" bad="status-warning" />
          )}
          {finger !== null && <Part icon="shield" label="البصمة" ok={finger} yes="تعمل" no="لا ترد" />}
          {card !== null && <Part icon="card" label="قارئ الكروت" ok={card} yes="يعمل" no="لا يرد" />}
        </>
      )}
    </section>
  );
}

// -------------------------------------------------------- headline numbers
function Kpi({ icon, label, value, note, tone, arrow, text }: {
  icon: string; label: string; value: string; note?: string; tone?: string; arrow?: string | null; text?: boolean;
}) {
  return (
    <div className="kpi">
      <div className="kpi-label"><Icon name={icon} />{label}</div>
      <div className={"kpi-value" + (text ? " text" : "")}>{value}</div>
      <div className={"kpi-note" + (tone ? " " + tone : "")}>{arrow && <Icon name={arrow} />}{note || ""}</div>
    </div>
  );
}

export function Kpis({ range, entries, denied, rings, alarms, rows, prev }: {
  range: Range; entries: DoorEvent[]; denied: DoorEvent[]; rings: DoorEvent[]; alarms: DoorEvent[];
  rows: DoorEvent[]; prev: { entry: number | null; denied: number | null };
}) {
  const today = range === "today";
  const than = today ? "عن أمس" : "عن الفترة السابقة";
  function delta(now: number, before: number | null, upIsBad: boolean | null): [string, string, string | null] {
    if (before === null || before === undefined) return ["", "", null];
    const diff = now - before;
    if (!diff) return ["مثل " + (today ? "أمس" : "الفترة السابقة"), "", null];
    const text = (diff > 0 ? "أكثر بـ " : "أقل بـ ") + n(Math.abs(diff)) + " " + than;
    const tone = upIsBad === null ? "" : (diff > 0) === upIsBad ? "bad" : "good";
    return [text, tone, diff > 0 ? "up" : "down"];
  }
  const d1 = delta(entries.length, prev.entry, null);
  const d2 = delta(denied.length, prev.denied, true);
  const opened = rows.filter((r) => r.event === "doorbell.answer" && /opened/.test(r.detail || "")).length;
  const last = entries[0];
  return (
    <section id="kpis" className="kpis" aria-label="الأرقام الرئيسية">
      <Kpi icon="door" label="الدخول" value={n(entries.length)} note={d1[0]} tone={d1[1]} arrow={d1[2]} />
      <Kpi icon="ban" label="محاولات مرفوضة" value={n(denied.length)} note={d2[0]} tone={d2[1]} arrow={d2[2]} />
      <Kpi icon="bell" label="رنّات الجرس" value={n(rings.length)}
           note={rings.length ? "فُتح الباب في " + n(opened) + " منها" : ""} />
      <Kpi icon="flame" label="إنذارات" value={n(alarms.length)}
           note={alarms.length ? "آخرها " + when(alarms[0].t!) : "لا إنذارات في الفترة"} />
      {last
        ? <Kpi icon="clock" label="آخر دخول" text note={when(last.t!)}
               value={(last.person ? last.person + " · " : "") + (METHOD[last.method] || last.method)} />
        : <Kpi icon="clock" label="آخر دخول" text value="لا يوجد" note="في هذه الفترة" />}
    </section>
  );
}

// --------------------------------------------------------- written analysis
export function Insights({ range, entries, denied, rings, alarms, rows }: {
  range: Range; entries: DoorEvent[]; denied: DoorEvent[]; rings: DoorEvent[]; alarms: DoorEvent[];
  rows: DoorEvent[];
}) {
  const lines: { icon: string; tone?: string; body: ReactNode }[] = [];
  const line = (icon: string, body: ReactNode, tone?: string) => lines.push({ icon, tone, body });
  if (!rows.length) {
    line("clock", "لا توجد أحداث مسجّلة في هذه الفترة.");
  } else {
    const days = RANGE_DAYS[range];
    if (entries.length) {
      if (days > 1) {
        line("door", <><b>{n(entries.length)}</b> دخول في الفترة، بمعدل <b>{(entries.length / days).toFixed(1)}</b> في اليوم.</>);
      } else {
        line("door", <><b>{n(entries.length)}</b> دخول اليوم حتى الآن.</>);
      }
      const byHour = new Array(24).fill(0);
      for (const r of entries) byHour[new Date(r.t!).getHours()]++;
      const best = byHour.indexOf(Math.max(...byHour));
      line("clock", <>أكثر وقت دخول بين <b>{hourLabel(best)}</b> و<b>{hourLabel((best + 1) % 24)}</b> ({times(byHour[best])}).</>);
      const methods = tally(entries, (r) => METHOD[r.method] || r.method || "أخرى");
      line("key", <>الطريقة الأكثر استخدامًا: <b>{methods[0].name}</b> بنسبة <b>{Math.round((methods[0].value / entries.length) * 100) + "%"}</b>.</>);
      const people = tally(entries, (r) => r.person);
      if (people.length) line("user", <>الأكثر دخولًا: <b>{people[0].name}</b> ({times(people[0].value)}).</>);
      const night = entries.filter((r) => new Date(r.t!).getHours() < 5).length;
      if (night) line("alert", <><b>{n(night)}</b> دخول بين منتصف الليل والخامسة فجرًا.</>, "status-warning");
      const cards = entries.filter((r) => r.method === "card").length;
      if (cards) line("card", <>فُتح الباب بالكرت <b>{times(cards)}</b>.</>);
      const remote = entries.filter((r) => r.method === "whatsapp").length;
      if (remote) line("chat", <>فُتح الباب من واتساب <b>{times(remote)}</b>.</>);
      const guests = entries.filter((r) => r.method === "temp_code").length;
      if (guests) line("key", <>استُخدم رمز مؤقت للضيوف <b>{times(guests)}</b>.</>);
    } else {
      line("door", "لا يوجد دخول في هذه الفترة.");
    }
    if (denied.length) {
      const kinds = tally(denied, (r) => METHOD[r.method] || r.method)
        .map((item) => item.name + " " + n(item.value)).join("، ");
      line("ban", <><b>{n(denied.length)}</b> محاولة مرفوضة ({kinds}).</>, "status-critical");
      const keypad = denied.filter((r) => r.event === "keypad.lockout").length;
      if (keypad) line("lock", <>قُفلت لوحة الأرقام <b>{times(keypad)}</b> بسبب تكرار الرمز الخاطئ.</>, "status-critical");
      const cardLocks = denied.filter((r) => r.event === "card.lockout").length;
      if (cardLocks) line("card", <>قُفل قارئ الكروت <b>{times(cardLocks)}</b> بسبب كروت غير مسجّلة.</>, "status-critical");
    } else {
      line("check", "لا توجد محاولات مرفوضة في هذه الفترة.", "status-good");
    }
    if (rings.length) {
      const answers = rows.filter((r) => r.event === "doorbell.answer");
      const opened = answers.filter((r) => /opened/.test(r.detail || "")).length;
      const declined = answers.filter((r) => /declined/.test(r.detail || "")).length;
      const silent = rows.filter((r) => r.event === "doorbell.timeout").length;
      line("bell", <>رنّ الجرس <b>{times(rings.length)}</b>: فُتح الباب <b>{n(opened)}</b>، رفض <b>{n(declined)}</b>، بدون رد <b>{n(silent)}</b>.</>);
      const waits: number[] = [];
      for (const answer of answers) {
        const ring = rings.find((r) => r.t! <= answer.t! && answer.t! - r.t! < 600000);
        if (ring) waits.push((answer.t! - ring.t!) / 1000);
      }
      if (waits.length) line("clock", <>متوسط وقت رد المالك على الجرس: <b>{seconds(sum(waits) / waits.length)}</b>.</>);
    }
    if (alarms.length) {
      line("flame", <><b>{n(alarms.length)}</b> إنذار في الفترة، آخرها <b>{when(alarms[0].t!)}</b>.</>, "status-critical");
    }
  }
  return (
    <ul id="insights" className="insights">
      {lines.map((l, i) => <li key={i}><Icon name={l.icon} className={l.tone} /><span>{l.body}</span></li>)}
    </ul>
  );
}
