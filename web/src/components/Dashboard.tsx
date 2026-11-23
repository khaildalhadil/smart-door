import { useCallback, useEffect, useRef, useState } from "react";
import { AUDIT_ROWS } from "../lib/config";
import { errorText, sessionLost } from "../lib/errors";
import { F_DAY, F_TIME, F_WEEKDAY, n } from "../lib/format";
import { activityBuckets, hourBuckets, rangeStart, split, tally } from "../lib/stats";
import type { Account, AuditRow, DoorEvent, DoorStatus, Me, Role, Source } from "../lib/types";
import { GROUPS, GROUP_OF, METHOD, RANGES, RANGE_DAYS, ROLE_NAME, ROLE_NOTE, type Range } from "../lib/wording";
import { AccountsCard, myAccountJob } from "./Accounts";
import { ChartTable, ColumnChart } from "./ColumnChart";
import type { GateMode } from "./Gate";
import { Icon } from "./Icons";
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
  onGate: (mode: GateMode, message?: string, email?: string) => void;
  onSignOut: () => void;
}) {
  const [me, setMe] = useState<Me>(firstMe);
  const role = me.role as Role;
  const admin = role === "admin";
  const [range, setRange] = useState<Range>(initialRange);
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(false);
  const [banner, setBanner] = useState<{ text: string; error: boolean } | null>(null);
  const [live, setLive] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [audit, setAudit] = useState<AuditRow[] | null>(null);
  const [accountsError, setAccountsError] = useState("");
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

  const showError = useCallback((err: unknown) => setBanner({ text: errorText(err), error: true }), []);

  const loadAccounts = useCallback(async () => {
    if (roleRef.current !== "admin") return;
    try {
      const [list, changes] = await Promise.all([source.members(), source.auditLog(AUDIT_ROWS)]);
      if (roleRef.current !== "admin") return;
      setAccounts(list);
      setAudit(changes);
      setAccountsError("");
    } catch (err) {
      setAccountsError(errorText(err));
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
        onGate("denied", "", who ? who.email : "");
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
        say("صلاحيتك الآن: " + ROLE_NAME[who.role]);
      }
      for (const r of rows) r.t = Date.parse(r.ts);
      setData({ rows, status, prev: { entry: prevEntry, denied: prevDenied }, loadedAt: Date.now() });
      setBanner(null);
      setNow(Date.now());
      if (who.role === "admin") void loadAccounts();
    } catch (err) {
      setBanner({ text: errorText(err), error: true });
      if (sessionLost(err)) onGate("login", errorText(err));
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
  const activity = activityBuckets(range, entries, (r, values) => {
    values[GROUPS.findIndex((g) => g.key === (GROUP_OF[r.method] || "other"))]++;
  }, GROUPS.length);
  const deniedBuckets = activityBuckets(range, denied, (_r, values) => { values[0]++; }, 1);
  const hours = hourBuckets(entries);
  const one = (name: string, color: string) => [{ name, color }];
  const openViewer = (list: DoorEvent[], at: number) => setViewer({ list, at });

  return (
    <div id="app" data-role={role}>
      <header className="topbar">
        <div className="brand">
          <Icon name="lock" className="icon-lg" />
          <div>
            <h1>القفل الذكي</h1>
            <p className="brand-sub">
              <span id="subtitle" className="muted">{source.demo ? "لوحة المتابعة · عرض تجريبي" : "لوحة المتابعة"}</span>
              <span id="role-chip" className={"role-chip " + role} title={ROLE_NOTE[role]}>
                <Icon name={admin ? "shield" : "user"} />{ROLE_NAME[role]}
              </span>
            </p>
          </div>
        </div>
        <div className="topbar-actions">
          <span id="live" className="live" hidden={!live}><span className="live-dot" />مباشر</span>
          <button id="refresh" className="button ghost" type="button" title="تحديث" onClick={() => void load()}>
            <Icon name="refresh" /><span className="hide-narrow">تحديث</span>
          </button>
          <button id="theme" className="button ghost" type="button" title="فاتح / داكن"
                  aria-label="تبديل الوضع الفاتح والداكن" onClick={toggleTheme}>
            <Icon name="theme" />
          </button>
          <button id="account" className="button ghost" type="button" title="حسابي"
                  onClick={() => setSheet(myAccountJob(source, me, role))}>
            <Icon name="user" /><span className="hide-narrow">حسابي</span>
          </button>
          {!source.demo && (
            <button id="logout" className="button ghost" type="button" title="خروج" onClick={onSignOut}>
              <Icon name="logout" /><span className="hide-narrow">خروج</span>
            </button>
          )}
        </div>
      </header>

      {source.demo ? (
        <p id="banner" className="banner" role="status">
          {"عرض تجريبي ببيانات وهمية، بصلاحية «" + ROLE_NAME[role] + "». "}
          <a href={"?demo=" + (admin ? "member" : "admin")}>
            {"اعرضها بصلاحية «" + ROLE_NAME[admin ? "member" : "admin"] + "»"}
          </a>
          {banner?.error && <><br />{banner.text}</>}
        </p>
      ) : (
        <p id="banner" className={"banner" + (banner?.error ? " error" : "")} role="status" hidden={!banner}>
          {banner?.text}
        </p>
      )}

      <Device status={data?.status ?? null} now={now} />

      {/* One filter row: it scopes every number, chart and list below. */}
      <nav className="filters" aria-label="الفترة">
        <div id="ranges" className="segmented" role="group" aria-label="الفترة الزمنية">
          {RANGES.map(([key, label]) => (
            <button key={key} type="button" data-range={key} aria-pressed={key === range}
                    onClick={() => setRange(key)}>{label}</button>
          ))}
        </div>
        <span id="range-note" className="muted">
          {today ? F_WEEKDAY.format(now) : "من " + F_DAY.format(rangeStart(range)) + " إلى اليوم"}
        </span>
      </nav>

      <main id="content" className={loading ? "loading" : ""}>
        <Kpis range={range} entries={entries} denied={denied} rings={rings} alarms={alarms} rows={rows}
              prev={data?.prev ?? { entry: null, denied: null }} />

        <div className="grid two">
          <figure className="card" id="card-activity">
            <figcaption>
              <h2 id="activity-title">{today ? "الدخول اليوم، ساعة بساعة" : "الدخول اليومي"}</h2>
              <p className="muted" id="activity-sub">عدد مرات فتح الباب، حسب طريقة الدخول</p>
            </figcaption>
            <div className="legend" id="activity-legend">
              {GROUPS.map((g) => <span key={g.key}><i className="swatch" style={{ background: g.color }} />{g.name}</span>)}
            </div>
            <ColumnChart id="activity-chart" buckets={activity} series={GROUPS} anchor={anchor}
                         summary={"الدخول: " + n(entries.length) + " في الفترة المختارة. استخدم السهمين للتنقل بين الأعمدة."} />
            <ChartTable id="activity-table" first={today ? "الساعة" : "اليوم"} buckets={activity} series={GROUPS} />
          </figure>

          <section className="card" id="card-insights">
            <h2>تحليل الفترة</h2>
            <Insights range={range} entries={entries} denied={denied} rings={rings} alarms={alarms} rows={rows} />
          </section>
        </div>

        <div className="grid three">
          {!today && (
            <figure className="card" id="card-hours">
              <figcaption>
                <h2>أوقات الدخول</h2>
                <p className="muted">عدد مرات الدخول في كل ساعة من اليوم</p>
              </figcaption>
              <ColumnChart id="hours-chart" buckets={hours} series={one("دخول", "var(--series-1)")} unit="دخول"
                           summary="عدد مرات الدخول في كل ساعة من اليوم." />
              <ChartTable id="hours-table" first="الساعة" buckets={hours} series={one("دخول", "var(--series-1)")} />
            </figure>
          )}
          <figure className="card">
            <figcaption>
              <h2>طريقة الدخول</h2>
              <p className="muted">كم مرة استُخدمت كل طريقة</p>
            </figcaption>
            <Ranked id="methods" items={tally(entries, (r) => METHOD[r.method] || r.method || "أخرى")}
                    unit="دخول" empty="لا يوجد دخول في هذه الفترة" />
          </figure>
          <figure className="card">
            <figcaption>
              <h2>من دخل</h2>
              <p className="muted">عدد مرات الدخول لكل شخص معروف</p>
            </figcaption>
            <Ranked id="people" items={tally(entries, (r) => r.person)} unit="دخول"
                    empty="لا يوجد دخول باسم معروف في هذه الفترة" />
          </figure>
        </div>

        <figure className="card" id="card-denied">
          <figcaption>
            <h2><Icon name="ban" className="status-critical" />
              <span id="denied-title">{today ? "المحاولات المرفوضة اليوم، ساعة بساعة" : "المحاولات المرفوضة يوميًا"}</span>
            </h2>
            <p className="muted" id="denied-note">
              {admin ? "رمز خاطئ، وجه أو بصمة أو كرت غير معروف، PIN خاطئ" : "رمز خاطئ، وجه أو بصمة أو كرت غير معروف"}
            </p>
          </figcaption>
          <ColumnChart id="denied-chart" buckets={deniedBuckets} series={one("محاولات مرفوضة", "var(--critical)")}
                       unit="محاولة مرفوضة" anchor={anchor} empty="لا توجد محاولات مرفوضة في هذه الفترة"
                       summary={"المحاولات المرفوضة: " + n(denied.length) + " في الفترة المختارة."} />
          <ChartTable id="denied-table" first={today ? "الساعة" : "اليوم"} buckets={deniedBuckets}
                      series={one("محاولات مرفوضة", "var(--critical)")} />
        </figure>

        {admin && <Gallery key={"gallery-" + range} source={source} rows={rows} onOpen={openViewer} onError={showError} />}

        <EventLog key={"log-" + range} rows={rows} admin={admin} onOpen={openViewer} />

        {/* Admins only; the database refuses everyone else anyway. */}
        {admin && (
          <AccountsCard source={source} accounts={accounts} audit={audit} error={accountsError}
                        reload={loadAccounts} openSheet={setSheet} />
        )}
      </main>

      <footer className="foot muted">
        <span id="foot-note">
          {data && "آخر تحميل " + F_TIME.format(data.loadedAt) + " · " + n(rows.length) + " حدث في الفترة" +
            (source.demo ? " · بيانات تجريبية" : "")}
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
