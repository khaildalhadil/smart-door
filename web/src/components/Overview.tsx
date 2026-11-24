import type { ReactNode } from "react";
import { ONLINE_WITHIN } from "../lib/config";
import { ago, hourLabel, n, plural, seconds, sum, times, when } from "../lib/format";
import { tr } from "../lib/i18n";
import { tally } from "../lib/stats";
import type { DoorEvent, DoorStatus } from "../lib/types";
import { RANGE_DAYS, methodName, type Range } from "../lib/wording";
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
      <section id="device" className="device" aria-label={tr("حالة الجهاز", "Device status")}>
        <Chip><i className="dot" />{tr("لم يصل أي تحديث من جهاز الباب بعد", "No update from the door yet")}</Chip>
      </section>
    );
  }
  const age = now - Date.parse(status.updated_at);
  const online = age < ONLINE_WITHIN;
  const info = status.info || {};
  const reader = (key: string) => (typeof info[key] === "boolean" ? (info[key] as boolean) : null);
  const card = reader("card"), finger = reader("fingerprint");
  const alarms = status.alarm ? status.alarm.split("+").map((k) => methodName(k)) : [];
  const working = tr("تعمل", "working");
  return (
    <section id="device" className="device" aria-label={tr("حالة الجهاز", "Device status")}>
      <Chip>
        <i className={"dot " + (online ? "good" : "critical")} />
        <b>{online ? tr("الجهاز متصل", "Door online") : tr("الجهاز غير متصل", "Door offline")}</b>
        {(online ? tr("آخر تحديث ", "last update ") : tr("آخر ظهور ", "last seen ")) + ago(age)}
      </Chip>
      {online && (
        // An old snapshot would only mislead: the rest only while the door reports in.
        <>
          {status.alarm && (
            <Chip>
              <Icon name="flame" className="status-critical" />
              <b>{tr("إنذار " + alarms.join(" و") + " الآن", "Alarm now: " + alarms.join(" + "))}</b>
            </Chip>
          )}
          <Chip>
            <Icon name="lock" />{tr("القفل", "Lock")}
            <b>{status.lock === "unlocked" ? tr("مفتوح", "unlocked") : tr("مقفل", "locked")}</b>
          </Chip>
          {status.camera !== null && status.camera !== undefined && (
            <Part icon="camera" label={tr("الكاميرا", "Camera")} ok={status.camera} yes={working}
                  no={tr("متوقفة", "off")} />
          )}
          {status.bot !== null && status.bot !== undefined && (
            <Part icon="chat" label={tr("بوت واتساب", "WhatsApp bot")} ok={status.bot}
                  yes={tr("متصل", "connected")} no={tr("غير متصل", "not connected")} bad="status-warning" />
          )}
          {finger !== null && (
            <Part icon="shield" label={tr("البصمة", "Fingerprint")} ok={finger} yes={working}
                  no={tr("لا ترد", "not responding")} />
          )}
          {card !== null && (
            <Part icon="card" label={tr("قارئ الكروت", "Card reader")} ok={card} yes={tr("يعمل", "working")}
                  no={tr("لا يرد", "not responding")} />
          )}
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
  function delta(now: number, before: number | null, upIsBad: boolean | null): [string, string, string | null] {
    if (before === null || before === undefined) return ["", "", null];
    const diff = now - before;
    if (!diff) {
      return [today ? tr("مثل أمس", "Same as yesterday") : tr("مثل الفترة السابقة", "Same as the period before"), "", null];
    }
    const text = tr((diff > 0 ? "أكثر بـ " : "أقل بـ ") + n(Math.abs(diff)) + (today ? " عن أمس" : " عن الفترة السابقة"),
                    n(Math.abs(diff)) + (diff > 0 ? " more" : " fewer") +
                    (today ? " than yesterday" : " than the period before"));
    const tone = upIsBad === null ? "" : (diff > 0) === upIsBad ? "bad" : "good";
    return [text, tone, diff > 0 ? "up" : "down"];
  }
  const d1 = delta(entries.length, prev.entry, null);
  const d2 = delta(denied.length, prev.denied, true);
  const opened = rows.filter((r) => r.event === "doorbell.answer" && /opened/.test(r.detail || "")).length;
  const last = entries[0];
  return (
    <section id="kpis" className="kpis" aria-label={tr("الأرقام الرئيسية", "Key numbers")}>
      <Kpi icon="door" label={tr("الدخول", "Entries")} value={n(entries.length)} note={d1[0]} tone={d1[1]} arrow={d1[2]} />
      <Kpi icon="ban" label={tr("محاولات مرفوضة", "Refused attempts")} value={n(denied.length)}
           note={d2[0]} tone={d2[1]} arrow={d2[2]} />
      <Kpi icon="bell" label={tr("رنّات الجرس", "Doorbell rings")} value={n(rings.length)}
           note={rings.length ? tr("فُتح الباب في " + n(opened) + " منها", "Door opened for " + n(opened) + " of them") : ""} />
      <Kpi icon="flame" label={tr("إنذارات", "Alarms")} value={n(alarms.length)}
           note={alarms.length ? tr("آخرها ", "Latest: ") + when(alarms[0].t!)
                               : tr("لا إنذارات في الفترة", "No alarms in this period")} />
      {last
        ? <Kpi icon="clock" label={tr("آخر دخول", "Last entry")} text note={when(last.t!)}
               value={(last.person ? last.person + " · " : "") + methodName(last.method)} />
        : <Kpi icon="clock" label={tr("آخر دخول", "Last entry")} text value={tr("لا يوجد", "None")}
               note={tr("في هذه الفترة", "in this period")} />}
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
  const entryWord = (value: number) => plural(value, "entry", "entries");
  if (!rows.length) {
    line("clock", tr("لا توجد أحداث مسجّلة في هذه الفترة.", "No events recorded in this period."));
  } else {
    const days = RANGE_DAYS[range];
    const e = entries.length;
    if (e) {
      if (days > 1) {
        const average = (e / days).toFixed(1);
        line("door", tr(<><b>{n(e)}</b> دخول في الفترة، بمعدل <b>{average}</b> في اليوم.</>,
                        <><b>{n(e)}</b> {entryWord(e)} in the period, <b>{average}</b> a day on average.</>));
      } else {
        line("door", tr(<><b>{n(e)}</b> دخول اليوم حتى الآن.</>, <><b>{n(e)}</b> {entryWord(e)} so far today.</>));
      }
      const byHour = new Array(24).fill(0);
      for (const r of entries) byHour[new Date(r.t!).getHours()]++;
      const best = byHour.indexOf(Math.max(...byHour));
      const from = <b>{hourLabel(best)}</b>, to = <b>{hourLabel((best + 1) % 24)}</b>;
      line("clock", tr(<>أكثر وقت دخول بين {from} و{to} ({times(byHour[best])}).</>,
                       <>Busiest hour: {from} to {to} ({times(byHour[best])}).</>));
      const methods = tally(entries, (r) => methodName(r.method) || tr("أخرى", "Other"));
      const share = Math.round((methods[0].value / e) * 100) + "%";
      line("key", tr(<>الطريقة الأكثر استخدامًا: <b>{methods[0].name}</b> بنسبة <b>{share}</b>.</>,
                     <>Most used method: <b>{methods[0].name}</b>, <b>{share}</b> of entries.</>));
      const people = tally(entries, (r) => r.person);
      if (people.length) {
        line("user", tr(<>الأكثر دخولًا: <b>{people[0].name}</b> ({times(people[0].value)}).</>,
                        <>Came in most: <b>{people[0].name}</b> ({times(people[0].value)}).</>));
      }
      const night = entries.filter((r) => new Date(r.t!).getHours() < 5).length;
      if (night) {
        line("alert", tr(<><b>{n(night)}</b> دخول بين منتصف الليل والخامسة فجرًا.</>,
                         <><b>{n(night)}</b> {entryWord(night)} between midnight and 5 a.m.</>), "status-warning");
      }
      const cards = entries.filter((r) => r.method === "card").length;
      if (cards) line("card", tr(<>فُتح الباب بالكرت <b>{times(cards)}</b>.</>, <>Opened with a card <b>{times(cards)}</b>.</>));
      const remote = entries.filter((r) => r.method === "whatsapp").length;
      if (remote) {
        line("chat", tr(<>فُتح الباب من واتساب <b>{times(remote)}</b>.</>, <>Opened from WhatsApp <b>{times(remote)}</b>.</>));
      }
      const guests = entries.filter((r) => r.method === "temp_code").length;
      if (guests) {
        line("key", tr(<>استُخدم رمز مؤقت للضيوف <b>{times(guests)}</b>.</>, <>A guest code was used <b>{times(guests)}</b>.</>));
      }
    } else {
      line("door", tr("لا يوجد دخول في هذه الفترة.", "No entries in this period."));
    }
    if (denied.length) {
      const kinds = tally(denied, (r) => methodName(r.method))
        .map((item) => item.name + " " + n(item.value)).join(tr("، ", ", "));
      line("ban", tr(<><b>{n(denied.length)}</b> محاولة مرفوضة ({kinds}).</>,
                     <><b>{n(denied.length)}</b> refused {plural(denied.length, "attempt", "attempts")} ({kinds}).</>),
           "status-critical");
      const keypad = denied.filter((r) => r.event === "keypad.lockout").length;
      if (keypad) {
        line("lock", tr(<>قُفلت لوحة الأرقام <b>{times(keypad)}</b> بسبب تكرار الرمز الخاطئ.</>,
                        <>The keypad locked itself <b>{times(keypad)}</b> after repeated wrong codes.</>), "status-critical");
      }
      const cardLocks = denied.filter((r) => r.event === "card.lockout").length;
      if (cardLocks) {
        line("card", tr(<>قُفل قارئ الكروت <b>{times(cardLocks)}</b> بسبب كروت غير مسجّلة.</>,
                        <>The card reader locked itself <b>{times(cardLocks)}</b> after unknown cards.</>), "status-critical");
      }
    } else {
      line("check", tr("لا توجد محاولات مرفوضة في هذه الفترة.", "No refused attempts in this period."), "status-good");
    }
    if (rings.length) {
      const answers = rows.filter((r) => r.event === "doorbell.answer");
      const opened = answers.filter((r) => /opened/.test(r.detail || "")).length;
      const declined = answers.filter((r) => /declined/.test(r.detail || "")).length;
      const silent = rows.filter((r) => r.event === "doorbell.timeout").length;
      line("bell", tr(<>رنّ الجرس <b>{times(rings.length)}</b>: فُتح الباب <b>{n(opened)}</b>، رفض <b>{n(declined)}</b>، بدون رد <b>{n(silent)}</b>.</>,
                      <>The doorbell rang <b>{times(rings.length)}</b>: opened <b>{n(opened)}</b>, turned away <b>{n(declined)}</b>, no answer <b>{n(silent)}</b>.</>));
      const waits: number[] = [];
      for (const answer of answers) {
        const ring = rings.find((r) => r.t! <= answer.t! && answer.t! - r.t! < 600000);
        if (ring) waits.push((answer.t! - ring.t!) / 1000);
      }
      if (waits.length) {
        const wait = <b>{seconds(sum(waits) / waits.length)}</b>;
        line("clock", tr(<>متوسط وقت رد المالك على الجرس: {wait}.</>, <>The owner answered the doorbell in {wait} on average.</>));
      }
    }
    if (alarms.length) {
      const latest = <b>{when(alarms[0].t!)}</b>;
      line("flame", tr(<><b>{n(alarms.length)}</b> إنذار في الفترة، آخرها {latest}.</>,
                       <><b>{n(alarms.length)}</b> {plural(alarms.length, "alarm", "alarms")} in the period, the latest {latest}.</>),
           "status-critical");
    }
  }
  return (
    <ul id="insights" className="insights">
      {lines.map((l, i) => <li key={i}><Icon name={l.icon} className={l.tone} /><span>{l.body}</span></li>)}
    </ul>
  );
}
