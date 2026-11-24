import { useCallback, useEffect, useRef, useState } from "react";
import { AUDIT_ROWS } from "../lib/config";
import { errorText, sessionLost } from "../lib/errors";
import { count, fmtDay, fmtTime, fmtWeekday, n } from "../lib/format";
import { tr, withLang } from "../lib/i18n";
import { activityBuckets, hourBuckets, rangeStart, split, tally } from "../lib/stats";
import type { Account, AuditRow, DoorEvent, DoorStatus, Me, Role, Source } from "../lib/types";
import { GROUP_OF, RANGE_DAYS, groups, methodName, ranges, roleName, roleNote, type Range } from "../lib/wording";
import { AccountsCard, myAccountJob } from "./Accounts";
import { ChartTable, ColumnChart } from "./ColumnChart";
import type { GateMode } from "./Gate";
import { Icon } from "./Icons";
import { LangButton } from "./LangButton";
import { Device, Insights, Kpis } from "./Overview";
import { Gallery, Viewer } from "./Photos";
import { EventLog } from "./EventLog";
import { Ranked } from "./Ranked";
import { Sheet, type SheetJob } from "./Sheet";

interface Data {
  rows: DoorEvent[];
  status: DoorStatus | null;
  prev: { entry: number | null; denied: number | null };
  loadedAt: number;
}

/** An error as it came: put in words while rendering, so it follows the language. */
type Failure = { err: unknown } | null;

function initialRange(): Range {
  const wanted = new URLSearchParams(window.location.search).get("range") as Range | null;
  return wanted && wanted in RANGE_DAYS ? wanted : "7d";
}

function toggleTheme() {
  const root = document.documentElement;
  const current = root.getAttribute("data-theme") ||
    (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = current === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try {
    localStorage.setItem("door-theme", next);
  } catch {
    /* private window */
  }
}

export function Dashboard({ source, me: firstMe, onGate, onSignOut }: {
  source: Source;
  me: Me;
  onGate: (mode: GateMode, error?: unknown, email?: string) => void;
  onSignOut: () => void;
}) {
  const [me, setMe] = useState<Me>(firstMe);
  const role = me.role as Role;
  const admin = role === "admin";
  const [range, setRange] = useState<Range>(initialRange);
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<Failure>(null);
  const [live, setLive] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [audit, setAudit] = useState<AuditRow[] | null>(null);
  const [accountsFailure, setAccountsFailure] = useState<Failure>(null);
  const [toast, setToast] = useState("");
  const [sheet, setSheet] = useState<SheetJob | null>(null);
  const [viewer, setViewer] = useState<{ list: DoorEvent[]; at: number } | null>(null);
  const rangeRef = useRef(range);
  const roleRef = useRef(role);
  rangeRef.current = range;
  roleRef.current = role;
  const toastTimer = useRef(0);

  const say = useCallback((text: string) => {
    setToast(text);
    clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(""), 5000);
  }, []);

  const showError = useCallback((err: unknown) => setFailure({ err }), []);

  const loadAccounts = useCallback(async () => {
    if (roleRef.current !== "admin") return;
    try {
      const [list, changes] = await Promise.all([source.members(), source.auditLog(AUDIT_ROWS)]);
      if (roleRef.current !== "admin") return;
      setAccounts(list);
      setAudit(changes);
      setAccountsFailure(null);
    } catch (err) {
      setAccountsFailure({ err });
    }
  }, [source]);

  const load = useCallback(async () => {
    const wanted = rangeRef.current;
    setLoading(true);   // the previous render stays, dimmed
    try {
      const from = rangeStart(wanted);
      const days = RANGE_DAYS[wanted];
      const before = new Date(from.getTime());
      before.setDate(before.getDate() - days);
      const fromISO = from.toISOString(), beforeISO = before.toISOString();
      // The role is asked again on every load: a change made by an admin
      // reaches an open page within a minute.
      const [rows, status, prevEntry, prevDenied, who] = await Promise.all([
        source.events(fromISO), source.status(), source.count("entry", beforeISO, fromISO),
        source.count("denied", beforeISO, fromISO), source.me()]);
      if (rangeRef.current !== wanted) return;   // the period changed meanwhile
      if (!who || !who.role) {
        onGate("denied", undefined, who ? who.email : "");
        return;
      }
      if (who.role !== roleRef.current) {
        roleRef.current = who.role;
        setMe(who);
        if (who.role !== "admin") {
          setAccounts(null);
          setAudit(null);
          setViewer(null);
        }
        say(tr("صلاحيتك الآن: ", "Your role is now: ") + roleName(who.role));
      }
      for (const r of rows) r.t = Date.parse(r.ts);
      setData({ rows, status, prev: { entry: prevEntry, denied: prevDenied }, loadedAt: Date.now() });
      setFailure(null);
      setNow(Date.now());
      if (who.role === "admin") void loadAccounts();
    } catch (err) {
      setFailure({ err });
      if (sessionLost(err)) onGate("login", err);
    } finally {
      setLoading(false);
    }
  }, [source, onGate, say, loadAccounts]);

  // Load on start and whenever the period changes.
  useEffect(() => { void load(); }, [range, load]);

  // Live rows, the heartbeat, and a slow poll in case a network blocks Realtime.
  useEffect(() => {
    source.subscribe(
      (row) => {
        if (!row || !row.uid || !row.ts) return;
        row.t = Date.parse(row.ts);
        if (row.t < rangeStart(rangeRef.current).getTime()) return;
        setData((d) => d && !d.rows.some((r) => r.uid === row.uid)
          ? { ...d, rows: [row, ...d.rows].sort((a, c) => c.t! - a.t!) } : d);
      },
      (status) => setData((d) => (d ? { ...d, status } : d)),
      setLive,
    );
    const poll = window.setInterval(() => void load(), 60000);
    const tick = window.setInterval(() => setNow(Date.now()), 15000);
    return () => {
      source.unsubscribe();
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [source, load]);

  const rows = data?.rows ?? [];
  const { entries, denied, rings, alarms } = split(rows);
  const today = range === "today";
  const anchor = today ? "start" : "end";
  const GROUPS = groups();
  const activity = activityBuckets(range, entries, (r, values) => {
    values[GROUPS.findIndex((g) => g.key === (GROUP_OF[r.method] || "other"))]++;
  }, GROUPS.length);
  const deniedBuckets = activityBuckets(range, denied, (_r, values) => { values[0]++; }, 1);
  const hours = hourBuckets(entries);
  const one = (name: string, color: string) => [{ name, color }];
  const openViewer = (list: DoorEvent[], at: number) => setViewer({ list, at });
  const entrySeries = one(tr("دخول", "Entries"), "var(--series-1)");
  const deniedSeries = one(tr("محاولات مرفوضة", "Refused attempts"), "var(--critical)");
  const hourOrDay = today ? tr("الساعة", "Hour") : tr("اليوم", "Day");
  const otherRole: Role = admin ? "member" : "admin";

  return (
    <div id="app" data-role={role}>
      <header className="topbar">
        <div className="brand">
          <Icon name="lock" className="icon-lg" />
          <div>
            <h1>{tr("القفل الذكي", "Smart Lock")}</h1>
            <p className="brand-sub">
              <span id="subtitle" className="muted">
                {tr("لوحة المتابعة", "Dashboard") + (source.demo ? tr(" · عرض تجريبي", " · demo") : "")}
              </span>
              <span id="role-chip" className={"role-chip " + role} title={roleNote(role)}>
                <Icon name={admin ? "shield" : "user"} />{roleName(role)}
              </span>
            </p>
          </div>
        </div>
        <div className="topbar-actions">
          <span id="live" className="live" hidden={!live}><span className="live-dot" />{tr("مباشر", "Live")}</span>
          <button id="refresh" className="button ghost" type="button" title={tr("تحديث", "Refresh")}
                  onClick={() => void load()}>
            <Icon name="refresh" /><span className="hide-narrow">{tr("تحديث", "Refresh")}</span>
          </button>
          <LangButton />
          <button id="theme" className="button ghost" type="button" title={tr("فاتح / داكن", "Light / dark")}
                  aria-label={tr("تبديل الوضع الفاتح والداكن", "Switch between light and dark")} onClick={toggleTheme}>
            <Icon name="theme" />
          </button>
          <button id="account" className="button ghost" type="button" title={tr("حسابي", "My account")}
                  onClick={() => setSheet(myAccountJob(source, me, role))}>
            <Icon name="user" /><span className="hide-narrow">{tr("حسابي", "My account")}</span>
          </button>
          {!source.demo && (
            <button id="logout" className="button ghost" type="button" title={tr("خروج", "Sign out")}
                    onClick={onSignOut}>
              <Icon name="logout" /><span className="hide-narrow">{tr("خروج", "Sign out")}</span>
            </button>
          )}
        </div>
      </header>

      {source.demo ? (
        <p id="banner" className="banner" role="status">
          {tr("عرض تجريبي ببيانات وهمية، بصلاحية «" + roleName(role) + "». ",
              "Demo with made-up data, as “" + roleName(role) + "”. ")}
          <a href={withLang("?demo=" + otherRole)}>
            {tr("اعرضها بصلاحية «" + roleName(otherRole) + "»", "See it as “" + roleName(otherRole) + "”")}
          </a>
          {failure && <><br />{errorText(failure.err)}</>}
        </p>
      ) : (
        <p id="banner" className={"banner" + (failure ? " error" : "")} role="status" hidden={!failure}>
          {failure && errorText(failure.err)}
        </p>
      )}

      <Device status={data?.status ?? null} now={now} />

      {/* One filter row: it scopes every number, chart and list below. */}
      <nav className="filters" aria-label={tr("الفترة", "Period")}>
        <div id="ranges" className="segmented" role="group" aria-label={tr("الفترة الزمنية", "Time period")}>
          {ranges().map(([key, label]) => (
            <button key={key} type="button" data-range={key} aria-pressed={key === range}
                    onClick={() => setRange(key)}>{label}</button>
          ))}
        </div>
        <span id="range-note" className="muted">
          {today ? fmtWeekday(now)
                 : tr("من " + fmtDay(rangeStart(range)) + " إلى اليوم", "From " + fmtDay(rangeStart(range)) + " to today")}
        </span>
      </nav>

      <main id="content" className={loading ? "loading" : ""}>
        <Kpis range={range} entries={entries} denied={denied} rings={rings} alarms={alarms} rows={rows}
              prev={data?.prev ?? { entry: null, denied: null }} />

        <div className="grid two">
          <figure className="card" id="card-activity">
            <figcaption>
              <h2 id="activity-title">
                {today ? tr("الدخول اليوم، ساعة بساعة", "Entries today, hour by hour") : tr("الدخول اليومي", "Entries per day")}
              </h2>
              <p className="muted" id="activity-sub">
                {tr("عدد مرات فتح الباب، حسب طريقة الدخول", "How many times the door was opened, by method")}
              </p>
            </figcaption>
            <div className="legend" id="activity-legend">
              {GROUPS.map((g) => <span key={g.key}><i className="swatch" style={{ background: g.color }} />{g.name}</span>)}
            </div>
            <ColumnChart id="activity-chart" buckets={activity} series={GROUPS} anchor={anchor}
                         summary={tr("الدخول: " + n(entries.length) + " في الفترة المختارة. استخدم السهمين للتنقل بين الأعمدة.",
                                     "Entries: " + n(entries.length) + " in the chosen period. Use the arrow keys to move between columns.")} />
            <ChartTable id="activity-table" first={hourOrDay} buckets={activity} series={GROUPS} />
          </figure>

          <section className="card" id="card-insights">
            <h2>{tr("تحليل الفترة", "The period at a glance")}</h2>
            <Insights range={range} entries={entries} denied={denied} rings={rings} alarms={alarms} rows={rows} />
          </section>
        </div>

        <div className="grid three">
          {!today && (
            <figure className="card" id="card-hours">
              <figcaption>
                <h2>{tr("أوقات الدخول", "Entry times")}</h2>
                <p className="muted">{tr("عدد مرات الدخول في كل ساعة من اليوم", "Entries in each hour of the day")}</p>
              </figcaption>
              <ColumnChart id="hours-chart" buckets={hours} series={entrySeries} unit={tr("دخول", "entries")}
                           summary={tr("عدد مرات الدخول في كل ساعة من اليوم.", "Entries in each hour of the day.")} />
              <ChartTable id="hours-table" first={tr("الساعة", "Hour")} buckets={hours} series={entrySeries} />
            </figure>
          )}
          <figure className="card">
            <figcaption>
              <h2>{tr("طريقة الدخول", "Entry method")}</h2>
              <p className="muted">{tr("كم مرة استُخدمت كل طريقة", "How often each method was used")}</p>
            </figcaption>
            <Ranked id="methods" items={tally(entries, (r) => methodName(r.method) || tr("أخرى", "Other"))}
                    unit={tr("دخول", "entries")} empty={tr("لا يوجد دخول في هذه الفترة", "No entries in this period")} />
          </figure>
          <figure className="card">
            <figcaption>
              <h2>{tr("من دخل", "Who came in")}</h2>
              <p className="muted">{tr("عدد مرات الدخول لكل شخص معروف", "Entries per known person")}</p>
            </figcaption>
            <Ranked id="people" items={tally(entries, (r) => r.person)} unit={tr("دخول", "entries")}
                    empty={tr("لا يوجد دخول باسم معروف في هذه الفترة", "No entries by a known person in this period")} />
          </figure>
        </div>

        <figure className="card" id="card-denied">
          <figcaption>
            <h2><Icon name="ban" className="status-critical" />
              <span id="denied-title">
                {today ? tr("المحاولات المرفوضة اليوم، ساعة بساعة", "Refused attempts today, hour by hour")
                       : tr("المحاولات المرفوضة يوميًا", "Refused attempts per day")}
              </span>
            </h2>
            <p className="muted" id="denied-note">
              {admin ? tr("رمز خاطئ، وجه أو بصمة أو كرت غير معروف، PIN خاطئ",
                          "Wrong code; unknown face, fingerprint or card; wrong PIN")
                     : tr("رمز خاطئ، وجه أو بصمة أو كرت غير معروف", "Wrong code; unknown face, fingerprint or card")}
            </p>
          </figcaption>
          <ColumnChart id="denied-chart" buckets={deniedBuckets} series={deniedSeries}
                       unit={tr("محاولة مرفوضة", "refused attempts")} anchor={anchor}
                       empty={tr("لا توجد محاولات مرفوضة في هذه الفترة", "No refused attempts in this period")}
                       summary={tr("المحاولات المرفوضة: " + n(denied.length) + " في الفترة المختارة.",
                                   "Refused attempts: " + n(denied.length) + " in the chosen period.")} />
          <ChartTable id="denied-table" first={hourOrDay} buckets={deniedBuckets} series={deniedSeries} />
        </figure>

        {admin && <Gallery key={"gallery-" + range} source={source} rows={rows} onOpen={openViewer} onError={showError} />}

        <EventLog key={"log-" + range} rows={rows} admin={admin} onOpen={openViewer} />

        {/* Admins only; the database refuses everyone else anyway. */}
        {admin && (
          <AccountsCard source={source} accounts={accounts} audit={audit}
                        error={accountsFailure ? errorText(accountsFailure.err) : ""}
                        reload={loadAccounts} openSheet={setSheet} />
        )}
      </main>

      <footer className="foot muted">
        <span id="foot-note">
          {data && tr("آخر تحميل ", "Loaded at ") + fmtTime(data.loadedAt) + " · " +
            count(rows.length, ["حدث واحد", "حدثان", "أحداث", "حدثًا"], ["event", "events"]) +
            tr(" في الفترة", " in the period") + (source.demo ? tr(" · بيانات تجريبية", " · demo data") : "")}
        </span>
      </footer>

      {viewer && admin && (
        <Viewer source={source} list={viewer.list} at={viewer.at} onError={showError}
                setAt={(at) => setViewer({ ...viewer, at })} onClose={() => setViewer(null)} />
      )}
      <Sheet job={sheet} onDone={(message) => { setSheet(null); if (message) say(message); }} />
      <p id="toast" className="toast" role="status" aria-live="polite" hidden={!toast}>{toast}</p>
    </div>
  );
}
