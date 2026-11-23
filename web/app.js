/* Smart Lock dashboard.
 *
 * Reads the door's history from Supabase (tables door_events / door_status,
 * bucket door-photos; see supabase/schema.sql) and shows it: headline numbers,
 * charts, a short written analysis, the camera pictures and the event log.
 * Nothing here can open the door or change the door's data.
 *
 * Two roles, decided by the database (door_members + row-level security in
 * supabase/schema.sql): an admin sees everything and manages the dashboard
 * accounts; a member sees the door's status, numbers and history, without
 * the camera pictures, the WhatsApp / panel / system records and the
 * accounts.  The page only leaves out what the database would return empty
 * or refuse anyway.
 *
 * Plain script, no build step: open index.html or put the web/ folder on any
 * static host.  index.html?demo (or ?demo=member) runs on made-up data
 * without Supabase.
 *
 * Everything that comes from the database is put on the page as text
 * (textContent), never as HTML.
 */
(function () {
  "use strict";

  var CFG = Object.assign({ url: "", key: "", table: "door_events", statusTable: "door_status",
                            bucket: "door-photos" }, window.DOOR_CONFIG || {});
  var COLUMNS = "uid,ts,category,method,person,event,detail,level,photo";
  var PAGE = 1000, MAX_PAGES = 20;          // rows fetched per request / at most
  var GALLERY_STEP = 24, LOG_STEP = 25;
  var ONLINE_WITHIN = 3 * 60 * 1000;        // heartbeat older than this = offline
  var SIGN_SECONDS = 3600;

  // ------------------------------------------------------------------ wording
  var CATEGORY = { entry: "دخول", denied: "مرفوض", doorbell: "جرس", alarm: "إنذار",
                   remote: "واتساب", admin: "إدارة", system: "النظام" };
  var ICON = { entry: "i-door", denied: "i-ban", doorbell: "i-bell", alarm: "i-flame",
               remote: "i-chat", admin: "i-gear", system: "i-gear" };
  var METHOD = { keypad: "رمز الباب", temp_code: "رمز مؤقت", face: "الوجه",
                 fingerprint: "البصمة", whatsapp: "واتساب", doorbell: "عبر الجرس",
                 menu: "لوحة التحكم", login: "PIN", fire: "حريق", smoke: "دخان",
                 test: "تجربة", camera: "الكاميرا", relay: "الريلاي", other: "أخرى" };
  // The daily chart shows three groups plus "other": three hues are what stays
  // distinguishable in every pairing, for every viewer, in both themes.
  var GROUPS = [
    { key: "bio", name: "وجه وبصمة", color: "var(--series-1)" },
    { key: "code", name: "رمز", color: "var(--series-2)" },
    { key: "remote", name: "عن بُعد (واتساب)", color: "var(--series-3)" },
    { key: "other", name: "أخرى", color: "var(--series-other)" }
  ];
  var GROUP_OF = { face: "bio", fingerprint: "bio", keypad: "code", temp_code: "code",
                   whatsapp: "remote", doorbell: "remote" };
  var COMMAND = { open: "فتح الباب", lock: "قفل الباب", status: "الحالة", tempcode: "رمز مؤقت",
                  photo: "صورة", log: "السجل", summary: "ملخص", help: "مساعدة",
                  bell_open: "فتح للزائر", bell_deny: "رفض الزائر", keep_shut: "عدم الفتح" };
  var ADMIN = { "session.login": "تسجيل دخول على اللوحة", "passcode.changed": "تغيير رمز الباب",
                "user.add": "إضافة مستخدم", "user.delete": "حذف مستخدم",
                "user.enabled": "تفعيل أو تعطيل مستخدم", "user.pin": "تغيير PIN",
                "face.add": "تسجيل وجه", "face.delete": "حذف وجه",
                "fingerprint.add": "تسجيل بصمة", "fingerprint.enroll": "تسجيل بصمة",
                "fingerprint.delete": "حذف بصمة", "fingerprint.erase": "مسح بصمة من الحساس",
                "fingerprint.clear": "مسح كل البصمات", "security.reset": "مسح الحظر",
                "app.exit": "إغلاق البرنامج" };
  var SYSTEM = { "app.start": "تشغيل النظام", "lock.shutdown": "إيقاف النظام",
                 "fingerprint.offline": "حساس البصمة لا يرد",
                 "fingerprint.online": "حساس البصمة رجع يعمل",
                 "fingerprint.fault": "خطأ في حساس البصمة", "fingerprint.error": "خطأ في حساس البصمة",
                 "lock.io": "عطل في ريلاي القفل", "api.auth": "طلب مرفوض على واجهة البوت",
                 "face.error": "خطأ في الكاميرا", "ui.exception": "خطأ في الواجهة",
                 "api.exception": "خطأ في واجهة البوت", "hazard.exception": "خطأ في حساس الإنذار" };
  var TABS = [["all", "الكل"], ["entry", "دخول"], ["denied", "مرفوض"], ["doorbell", "جرس"],
              ["alarm", "إنذار"]];
  var RANGE_DAYS = { today: 1, "7d": 7, "30d": 30, "90d": 90 };

  // -------------------------------------------------------------------- roles
  var ROLE_NAME = { admin: "مدير", member: "مستخدم" };
  var ROLE_NOTE = { admin: "كل الصلاحيات", member: "صلاحيات محدودة" };
  // What each role may do: [what, admin, member].  The same table as the top
  // of supabase/schema.sql, where it is enforced.
  var PERMISSIONS = [
    ["حالة الباب الآن", true, true],
    ["الأرقام والرسوم وتحليل الفترة", true, true],
    ["سجل الدخول والمحاولات المرفوضة والجرس والإنذارات", true, true],
    ["تغيير كلمة مرور حسابه", true, true],
    ["صور الكاميرا", true, false],
    ["أوامر واتساب وسجل الإدارة والنظام", true, false],
    ["إدارة الحسابات والصلاحيات وسجل تغييراتها", true, false]
  ];
  var MEMBER_DENIED = ["keypad", "face", "fingerprint"];   // refusals a member sees
  var EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  var MIN_PASSWORD = 8, MAX_PASSWORD_BYTES = 72;
  var AUDIT_ROWS = 20;
  var AUDIT_ICON = { "member.add": "i-user", "member.role": "i-shield", "member.remove": "i-trash",
                     "password.reset": "i-key" };

  /** The "door members read events" policy, repeated for the demo only. */
  function visibleTo(role, r) {
    if (role === "admin") return true;
    if (role !== "member") return false;
    return ["entry", "doorbell", "alarm"].indexOf(r.category) >= 0 ||
           (r.category === "denied" && MEMBER_DENIED.indexOf(r.method) >= 0);
  }

  // -------------------------------------------------------------------- state
  var state = { range: "7d", rows: [], status: null, prev: { entry: null, denied: null },
                source: null, demo: false, galleryTab: "all", galleryShown: GALLERY_STEP,
                logTab: "all", logPage: 0, search: "", loadedAt: 0, viewer: [], viewerAt: 0,
                me: null, role: null, accounts: null, audit: null };
  var signed = new Map();      // bucket path -> { url, until }
  var timers = [];

  // ---------------------------------------------------------------- DOM tools
  function $(id) { return document.getElementById(id); }
  function isAdmin() { return state.role === "admin"; }
  /** Keeps an e-mail address in one piece inside Arabic text. */
  function isolate(text) { return "\u2068" + text + "\u2069"; }

  function add(node, children) {
    for (var i = 0; i < children.length; i++) {
      var child = children[i];
      if (child === null || child === undefined || child === false) continue;
      if (Array.isArray(child)) add(node, child);
      else node.appendChild(typeof child === "object" ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  /** el("p", {class: "x"}, "text", node, ...): children are text unless they are nodes. */
  function el(tag, attrs) {
    var node = document.createElement(tag);
    for (var key in attrs || {}) {
      if (attrs[key] === null || attrs[key] === undefined || attrs[key] === false) continue;
      if (key === "style") node.style.cssText = attrs[key];
      else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2), attrs[key]);
      else node.setAttribute(key, attrs[key] === true ? "" : attrs[key]);
    }
    return add(node, Array.prototype.slice.call(arguments, 2));
  }

  function svg(tag, attrs) {
    var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var key in attrs || {}) {
      if (key === "style") node.style.cssText = attrs[key];
      else node.setAttribute(key, attrs[key]);
    }
    return add(node, Array.prototype.slice.call(arguments, 2));
  }

  function icon(name, extra) {
    var node = svg("svg", { "class": "icon" + (extra ? " " + extra : ""), viewBox: "0 0 24 24",
                            "aria-hidden": "true" });
    var use = svg("use");
    use.setAttribute("href", "#" + name);
    node.appendChild(use);
    return node;
  }

  function b(text) { return el("b", null, text); }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function sum(list) { return list.reduce(function (a, x) { return a + x; }, 0); }

  // --------------------------------------------------------------- formatting
  var LOCALE = "ar-u-nu-latn";
  var NUM = new Intl.NumberFormat("en-US");
  var F_DAY = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "long" });
  var F_WEEKDAY = new Intl.DateTimeFormat(LOCALE, { weekday: "long", day: "numeric", month: "long" });
  var F_TIME = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit", hour12: false });
  var F_FULL = new Intl.DateTimeFormat(LOCALE, { weekday: "long", day: "numeric", month: "long",
                                                 year: "numeric", hour: "2-digit", minute: "2-digit",
                                                 hour12: false });

  function n(value) { return NUM.format(value); }

  /** Arabic number agreement: 1, 2, 3-10, 11+. */
  function count(value, one, two, few, many) {
    if (value === 1) return one;
    if (value === 2) return two;
    return n(value) + " " + (value >= 3 && value <= 10 ? few : many);
  }
  function times(value) { return count(value, "مرة واحدة", "مرتين", "مرات", "مرة"); }

  function startOfDay(t) { var d = new Date(t); d.setHours(0, 0, 0, 0); return d; }

  function when(t) {
    var day = startOfDay(t).getTime(), today = startOfDay(Date.now()).getTime();
    var clock = F_TIME.format(t);
    if (day === today) return "اليوم " + clock;
    if (day === today - 86400000) return "أمس " + clock;
    return F_DAY.format(t) + " " + clock;
  }

  function ago(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return "قبل " + count(s, "ثانية", "ثانيتين", "ثوانٍ", "ثانية");
    var m = Math.round(s / 60);
    if (m < 60) return "قبل " + count(m, "دقيقة", "دقيقتين", "دقائق", "دقيقة");
    var h = Math.round(m / 60);
    if (h < 48) return "قبل " + count(h, "ساعة", "ساعتين", "ساعات", "ساعة");
    return "قبل " + count(Math.round(h / 24), "يوم", "يومين", "أيام", "يومًا");
  }

  function seconds(s) {
    s = Math.round(s);
    if (s < 60) return count(s, "ثانية", "ثانيتين", "ثوانٍ", "ثانية");
    return count(Math.round(s / 60), "دقيقة", "دقيقتين", "دقائق", "دقيقة");
  }

  function hourLabel(h) { return (h < 10 ? "0" : "") + h + ":00"; }

  function describe(r) {
    var how = METHOD[r.method] || r.method || "";
    var detail = r.detail || "";
    switch (r.category) {
      case "entry":
        return "دخول — " + (how || "غير محدد");
      case "denied":
        if (r.method === "whatsapp") return "أمر من رقم غير مصرّح";
        if (r.event === "keypad.lockout") return "قفل لوحة الأرقام بعد تكرار الرمز الخاطئ";
        if (r.method === "keypad") return "رمز خاطئ";
        if (r.method === "face") return /disabled/.test(detail) ? "وجه حسابه معطّل" : "وجه غير معروف";
        if (r.method === "fingerprint") return /disabled/.test(detail) ? "بصمة حسابها معطّل" : "بصمة غير مسجّلة";
        if (r.event === "login.lockout") return "قفل تسجيل الدخول بعد تكرار PIN الخاطئ";
        if (r.method === "login") return "PIN خاطئ على اللوحة";
        return "محاولة مرفوضة";
      case "doorbell":
        if (r.event === "doorbell.ring") return /again/.test(detail) ? "رنّ الجرس مرة ثانية" : "رنّ الجرس";
        if (r.event === "doorbell.timeout") return "الجرس: لم يرد المالك";
        if (/opened/.test(detail)) return "الجرس: المالك فتح الباب";
        if (/declined/.test(detail)) return "الجرس: المالك رفض";
        return "الجرس";
      case "alarm":
        var what = r.method === "smoke" ? "الدخان" : "الحريق";
        if (/alarm over/.test(detail)) return "انتهى إنذار " + what;
        if (r.event === "lock.unlock") return "فُتح الباب بسبب إنذار " + what;
        return "إنذار " + (r.method === "smoke" ? "دخان" : "حريق");
      case "remote":
        if (r.event === "tempcode.add") return "رمز مؤقت جديد";
        return "أمر واتساب: " + (COMMAND[detail.split(" ")[0]] || detail.split(" ")[0]);
      case "admin":
        return ADMIN[r.event] || r.event;
      default:
        if (r.event === "face.camera") return /offline/.test(detail) ? "الكاميرا انفصلت" : "الكاميرا رجعت تعمل";
        return SYSTEM[r.event] || r.event;
    }
  }

  function tag(r) {
    return el("span", { "class": "tag " + r.category }, icon(ICON[r.category] || "i-gear"),
              CATEGORY[r.category] || r.category);
  }

  // Error codes from the database functions, the door-accounts function and
  // Supabase Auth, in words.
  var ERRORS = {
    not_admin: "هذا الإجراء للمدير فقط.",
    self_change: "لا يمكنك تغيير صلاحية حسابك أو حذفه بنفسك.",
    bad_role: "صلاحية غير معروفة.",
    no_user: "الحساب غير موجود (ربما حُذف).",
    email_exists: "يوجد حساب بهذا البريد. يمكنك منحه صلاحية من القائمة.",
    weak_password: "كلمة المرور ضعيفة: 8 أحرف على الأقل، ويُفضّل حروف وأرقام.",
    long_password: "كلمة المرور طويلة جدًا.",
    same_password: "كلمة المرور الجديدة هي نفس الحالية.",
    mismatch: "كلمتا المرور غير متطابقتين.",
    bad_email: "البريد الإلكتروني غير صحيح.",
    delete_failed: "سُحبت صلاحية الحساب، لكن تعذّر حذفه نهائيًا. حاول مرة أخرى.",
    auth_error: "خدمة الحسابات في Supabase لم ترد. حاول بعد قليل.",
    server_error: "خطأ في الخادم. حاول بعد قليل.",
    no_session: "انتهت الجلسة. سجّل الدخول من جديد.",
    bad_request: "طلب غير صحيح.",
    function_missing: "دالة الحسابات door-accounts غير منشورة في Supabase بعد (الخطوات في README)."
  };

  function errorText(err) {
    var message = (err && (err.message || err.error_description)) || String(err);
    var code = err && err.code;
    if (Object.prototype.hasOwnProperty.call(ERRORS, message)) return ERRORS[message];
    if (code && Object.prototype.hasOwnProperty.call(ERRORS, code)) return ERRORS[code];
    if (/Failed to send a request to the Edge Function|Requested function was not found/i.test(message)) {
      return ERRORS.function_missing;
    }
    if (/Could not find the function|PGRST202/i.test(message)) {
      return "قاعدة البيانات غير مهيأة لهذه النسخة: نفّذ supabase/schema.sql في SQL Editor.";
    }
    if (/Invalid login credentials/i.test(message)) return "البريد أو كلمة المرور غير صحيحة.";
    if (/Email not confirmed/i.test(message)) return "هذا البريد لم يُؤكَّد بعد. أكّده من لوحة Supabase.";
    if (/Failed to fetch|NetworkError|Load failed/i.test(message)) return "تعذّر الاتصال بقاعدة البيانات. تحقق من الإنترنت.";
    if (/JWT|expired|401/i.test(message)) return "انتهت الجلسة. سجّل الدخول من جديد.";
    if (/permission denied|42501/i.test(message)) return "لا توجد صلاحية لهذا. تأكد أن supabase/schema.sql منفّذ كاملًا.";
    if (/does not exist|42P01|PGRST205/i.test(message)) return "الجداول غير موجودة. نفّذ supabase/schema.sql في SQL Editor.";
    return "خطأ: " + message;
  }

  // ------------------------------------------------------------------ tooltip
  var tip = $("tooltip");

  /** lines: [{ value, label, color }]; (x, y) in viewport pixels. */
  function showTip(title, lines, x, y) {
    clear(tip);
    if (title) tip.appendChild(el("div", { "class": "tooltip-title" }, title));
    lines.forEach(function (line) {
      tip.appendChild(el("div", { "class": "tooltip-row" },
        line.color ? el("i", { "class": "tooltip-key", style: "background:" + line.color }) : null,
        el("b", null, line.value), el("span", null, line.label)));
    });
    tip.hidden = false;
    var box = tip.getBoundingClientRect();
    var left = Math.min(Math.max(8, x - box.width / 2), window.innerWidth - box.width - 8);
    var top = y - box.height - 12;
    if (top < 8) top = y + 18;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  function hideTip() { tip.hidden = true; }

  // ------------------------------------------------------------------- charts
  function niceStep(max) {
    var steps = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000];
    for (var i = 0; i < steps.length; i++) if (max / steps[i] <= 4) return steps[i];
    return Math.ceil(max / 4);
  }

  function roundedTop(x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h));
    return "M" + x + "," + (y + h) + "V" + (y + r) + "Q" + x + "," + y + " " + (x + r) + "," + y +
           "H" + (x + w - r) + "Q" + (x + w) + "," + y + " " + (x + w) + "," + (y + r) +
           "V" + (y + h) + "Z";
  }

  /**
   * Stacked columns on one baseline.
   *   host            the .chart element (focusable: arrow keys walk the columns)
   *   opts.buckets    [{ label, title, values: [one per series] }]
   *   opts.series     [{ name, color }]
   *   opts.unit       noun after the number in the tooltip
   *   opts.anchor     "end": the last column always gets its axis label
   *   opts.summary    sentence for screen readers
   */
  function columns(host, opts) {
    host._chart = opts;
    if (!host._observer && window.ResizeObserver) {
      host._observer = new ResizeObserver(function () {
        if (host._chart && host._width !== host.clientWidth) drawColumns(host, host._chart);
      });
      host._observer.observe(host);
      host.addEventListener("keydown", function (event) { chartKey(host, event); });
      host.addEventListener("focus", function () {
        if (host._bands && host._bands.length) host._activate(host._active >= 0 ? host._active : host._bands.length - 1);
      });
      host.addEventListener("blur", function () { if (host._deactivate) host._deactivate(); });
    }
    drawColumns(host, opts);
  }

  function chartKey(host, event) {
    if (!host._bands || !host._bands.length) return;
    var last = host._bands.length - 1, at = host._active < 0 ? last : host._active;
    if (event.key === "ArrowRight") at = Math.min(last, at + 1);
    else if (event.key === "ArrowLeft") at = Math.max(0, at - 1);
    else if (event.key === "Home") at = 0;
    else if (event.key === "End") at = last;
    else if (event.key === "Escape") { host._deactivate(); return; }
    else return;
    event.preventDefault();
    host._activate(at);
  }

  function drawColumns(host, opts) {
    clear(host);
    hideTip();
    host._bands = [];
    host._active = -1;
    host._width = host.clientWidth;
    host.setAttribute("aria-label", opts.summary || "");
    var buckets = opts.buckets, series = opts.series;
    var totals = buckets.map(function (bucket) { return sum(bucket.values); });
    var max = Math.max.apply(null, totals.concat([0]));
    if (!max) {
      host.appendChild(el("p", { "class": "empty" }, opts.empty || "لا توجد بيانات في هذه الفترة"));
      return;
    }
    var width = Math.max(220, host.clientWidth);
    var top = 18, plot = 180, bottom = 24, left = 30, right = 6;
    var step = niceStep(max), ceiling = Math.ceil(max / step) * step;
    var band = (width - left - right) / buckets.length;
    var bar = Math.max(1.5, Math.min(24, band > 7 ? band - 4 : band - 1));
    function y(value) { return top + plot - (value / ceiling) * plot; }

    var root = svg("svg", { viewBox: "0 0 " + width + " " + (top + plot + bottom),
                            height: top + plot + bottom, "aria-hidden": "true" });
    for (var v = step; v <= ceiling; v += step) {
      root.appendChild(svg("line", { "class": "gridline", x1: left, x2: width - right, y1: y(v), y2: y(v) }));
      root.appendChild(svg("text", { x: left - 6, y: y(v) + 4, "text-anchor": "end" }, n(v)));
    }
    root.appendChild(svg("text", { x: left - 6, y: y(0) + 4, "text-anchor": "end" }, "0"));

    var every = Math.max(1, Math.ceil(buckets.length / Math.max(1, Math.floor((width - left - right) / 46))));
    var peak = totals.lastIndexOf(max);
    buckets.forEach(function (bucket, i) {
      var x = left + i * band + (band - bar) / 2, below = 0;
      var lastWithValue = -1;
      bucket.values.forEach(function (value, s) { if (value > 0) lastWithValue = s; });
      bucket.values.forEach(function (value, s) {
        if (value <= 0) return;
        var y0 = y(below), y1 = y(below + value);
        below += value;
        var isTop = s === lastWithValue;
        // A 2px gap in the surface colour separates the segments of a stack.
        var gap = isTop ? 0 : Math.min(2, Math.max(0, y0 - y1 - 1));
        var height = Math.max(1, y0 - y1 - gap);
        var style = "fill:" + series[s].color;
        root.appendChild(isTop
          ? svg("path", { d: roundedTop(x, y1, bar, height, 4), style: style })
          : svg("rect", { x: x, y: y1 + gap, width: bar, height: height, style: style }));
      });
      var labelled = opts.anchor === "end" ? (buckets.length - 1 - i) % every === 0 : i % every === 0;
      if (labelled) {
        root.appendChild(svg("text", { x: left + i * band + band / 2, y: top + plot + 16,
                                       "text-anchor": "middle" }, bucket.label));
      }
      if (i === peak) {
        root.appendChild(svg("text", { "class": "peak", x: left + i * band + band / 2,
                                       y: y(max) - 5, "text-anchor": "middle" }, n(max)));
      }
    });
    root.appendChild(svg("line", { "class": "baseline", x1: left, x2: width - right, y1: y(0), y2: y(0) }));

    // The whole column slot is the hover target, not just the painted bar.
    buckets.forEach(function (bucket, i) {
      var hit = svg("rect", { "class": "band", x: left + i * band, y: top, width: band, height: plot });
      hit.addEventListener("pointerenter", function () { host._activate(i); });
      hit.addEventListener("pointerdown", function () { host._activate(i); });
      root.appendChild(hit);
      host._bands.push(hit);
    });
    root.addEventListener("pointerleave", function () { host._deactivate(); });
    host.appendChild(root);

    host._activate = function (i) {
      if (host._active >= 0 && host._bands[host._active]) host._bands[host._active].classList.remove("active");
      host._active = i;
      var hit = host._bands[i], bucket = buckets[i];
      if (!hit) return;
      hit.classList.add("active");
      var lines = [];
      if (series.length > 1) {
        series.forEach(function (one, s) {
          lines.push({ value: n(bucket.values[s]), label: one.name, color: one.color });
        });
        lines.push({ value: n(totals[i]), label: "المجموع" });
      } else {
        lines.push({ value: n(totals[i]), label: opts.unit || series[0].name, color: series[0].color });
      }
      var box = hit.getBoundingClientRect();
      var tipY = box.top + (y(totals[i]) - top) * (box.height / plot);
      showTip(bucket.title, lines, box.left + box.width / 2, tipY);
    };
    host._deactivate = function () {
      if (host._active >= 0 && host._bands[host._active]) host._bands[host._active].classList.remove("active");
      host._active = -1;
      hideTip();
    };
  }

  /** The table twin of a column chart: every value, readable without hovering. */
  function chartTable(host, firstHeading, opts) {
    clear(host);
    var multi = opts.series.length > 1;
    var head = el("tr", null, el("th", null, firstHeading),
                  opts.series.map(function (one) { return el("th", null, one.name); }),
                  multi ? el("th", null, "المجموع") : null);
    var body = el("tbody");
    opts.buckets.slice().reverse().forEach(function (bucket) {
      body.appendChild(el("tr", null, el("td", null, bucket.title),
        bucket.values.map(function (value) { return el("td", null, n(value)); }),
        multi ? el("td", null, n(sum(bucket.values))) : null));
    });
    host.appendChild(el("div", null, el("table", null, el("thead", null, head), body)));
  }

  /** Ranked horizontal bars: one hue, largest first, value at the tip. */
  function ranked(host, items, unit, emptyText) {
    clear(host);
    if (!items.length) {
      host.appendChild(el("p", { "class": "empty" }, emptyText));
      return;
    }
    var max = items[0].value, total = sum(items.map(function (item) { return item.value; }));
    items.forEach(function (item) {
      var share = Math.round(item.value / total * 100);
      var row = el("div", { "class": "ranked-row", tabindex: "0", role: "group",
                            "aria-label": item.name + ": " + n(item.value) + " " + unit + "، " + share + "%" },
        el("span", { "class": "ranked-name" }, item.name),
        el("div", { "class": "ranked-track" },
          el("div", { "class": "ranked-bar",
                      style: "width:calc((100% - 86px) * " + (item.value / max).toFixed(4) + ")" }),
          el("span", { "class": "ranked-value" }, n(item.value), el("small", null, " · " + share + "%"))));
      function show() {
        var box = row.getBoundingClientRect();
        showTip(item.name, [{ value: n(item.value), label: unit + " · " + share + "% من المجموع",
                              color: "var(--series-1)" }], box.left + box.width / 2, box.top);
      }
      row.addEventListener("pointerenter", show);
      row.addEventListener("focus", show);
      row.addEventListener("pointerleave", hideTip);
      row.addEventListener("blur", hideTip);
      host.appendChild(row);
    });
  }

  function tally(rows, keyOf) {
    var map = new Map();
    rows.forEach(function (r) {
      var key = keyOf(r);
      if (key) map.set(key, (map.get(key) || 0) + 1);
    });
    return Array.from(map, function (pair) { return { name: pair[0], value: pair[1] }; })
      .sort(function (a, c) { return c.value - a.value || (a.name < c.name ? -1 : 1); });
  }

  // ----------------------------------------------------------------- sources
  var Cloud = {
    client: null,
    init: function () {
      this.client = window.supabase.createClient(CFG.url, CFG.key, {
        auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false }
      });
      // Signed out in another tab, or the account was deleted: back to the form.
      this.client.auth.onAuthStateChange(function (event) {
        if (event === "SIGNED_OUT" && state.source === Cloud) {
          setTimeout(function () { showGate("login"); }, 0);
        }
      });
    },
    session: async function () {
      var result = await this.client.auth.getSession();
      return result.data ? result.data.session : null;
    },
    signIn: async function (email, password) {
      var result = await this.client.auth.signInWithPassword({ email: email, password: password });
      if (result.error) throw result.error;
      return result.data.session;
    },
    signOut: function () { return this.client.auth.signOut(); },
    /** { user_id, email, role }: role is "admin", "member" or null (no access). */
    me: async function () {
      var result = await this.client.rpc("door_me");
      if (result.error) throw result.error;
      return result.data;
    },
    changePassword: async function (password) {
      var result = await this.client.auth.updateUser({ password: password });
      if (result.error) throw result.error;
    },
    // Admins only; the database refuses everyone else.
    members: async function () {
      var result = await this.client.rpc("door_list_members");
      if (result.error) throw result.error;
      return result.data || [];
    },
    auditLog: async function (limit) {
      var result = await this.client.from("door_audit")
        .select("id,ts,actor_email,action,target_email,old_role,new_role")
        .order("ts", { ascending: false }).limit(limit);
      if (result.error) throw result.error;
      return result.data || [];
    },
    setRole: async function (userId, role) {
      var result = await this.client.rpc("door_set_role", { p_user: userId, p_role: role });
      if (result.error) throw result.error;
    },
    /** The door-accounts Edge Function: things that need Supabase's secret key. */
    accounts: async function (body) {
      var result = await this.client.functions.invoke("door-accounts", { body: body });
      if (!result.error) return result.data;
      var code = "", response = result.error.context;
      if (response && typeof response.json === "function") {
        try {
          var payload = await response.json();
          code = (payload && payload.error) || "";
        } catch (err) { /* not JSON */ }
        if (!code && response.status === 404) code = "function_missing";
      }
      throw new Error(code || result.error.message);
    },
    createAccount: function (email, password, role) {
      return this.accounts({ action: "create", email: email, password: password, role: role });
    },
    deleteAccount: function (userId) {
      return this.accounts({ action: "delete", user_id: userId });
    },
    resetPassword: function (userId, password) {
      return this.accounts({ action: "password", user_id: userId, password: password });
    },
    events: async function (fromISO) {
      var out = [];
      for (var page = 0; page < MAX_PAGES; page++) {
        var result = await this.client.from(CFG.table).select(COLUMNS).gte("ts", fromISO)
          .order("ts", { ascending: false }).range(page * PAGE, page * PAGE + PAGE - 1);
        if (result.error) throw result.error;
        out = out.concat(result.data);
        if (result.data.length < PAGE) break;
      }
      return out;
    },
    count: async function (category, fromISO, toISO) {
      var result = await this.client.from(CFG.table).select("uid", { count: "exact", head: true })
        .eq("category", category).gte("ts", fromISO).lt("ts", toISO);
      if (result.error) throw result.error;
      return result.count;
    },
    status: async function () {
      var result = await this.client.from(CFG.statusTable).select("*").eq("id", "door").maybeSingle();
      if (result.error) throw result.error;
      return result.data;
    },
    sign: async function (paths) {
      var result = await this.client.storage.from(CFG.bucket).createSignedUrls(paths, SIGN_SECONDS);
      if (result.error) throw result.error;
      var map = {};
      result.data.forEach(function (item) { if (item.signedUrl) map[item.path] = item.signedUrl; });
      return map;
    },
    subscribe: function (onEvent, onStatus, onState) {
      this.channel = this.client.channel("door-dashboard")
        .on("postgres_changes", { event: "INSERT", schema: "public", table: CFG.table },
            function (payload) { onEvent(payload["new"]); })
        .on("postgres_changes", { event: "*", schema: "public", table: CFG.statusTable },
            function (payload) { if (payload["new"] && payload["new"].id) onStatus(payload["new"]); })
        .subscribe(function (status) { onState(status === "SUBSCRIBED"); });
    },
    unsubscribe: function () {
      if (this.channel) this.client.removeChannel(this.channel);
      this.channel = null;
    }
  };

  /** Made-up history, so the page can be seen before Supabase is set up. */
  var Demo = (function () {
    var rows = null, seedValue = 20261113;
    function random() {              // mulberry32: the same "history" on every load
      seedValue |= 0; seedValue = seedValue + 0x6D2B79F5 | 0;
      var t = Math.imul(seedValue ^ seedValue >>> 15, 1 | seedValue);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    }
    function pick(list) { return list[Math.floor(random() * list.length)]; }
    function picture(category, t) {
      var tint = { entry: "#3f6f58", denied: "#7a4040", doorbell: "#3d5a80", alarm: "#8a5a2b" }[category] || "#555";
      var shift = Math.round(random() * 60 - 30);
      var text = F_TIME.format(t);
      var markup = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 240">' +
        '<rect width="320" height="240" fill="#2b3038"/><rect y="170" width="320" height="70" fill="#22262d"/>' +
        '<circle cx="' + (160 + shift) + '" cy="96" r="38" fill="#c9a98f"/>' +
        '<rect x="' + (100 + shift) + '" y="140" width="120" height="100" rx="18" fill="' + tint + '"/>' +
        '<text x="12" y="228" font-family="monospace" font-size="15" fill="#fff">' + text + '</text></svg>';
      return "data:image/svg+xml," + encodeURIComponent(markup);
    }
    function build() {
      rows = [];
      var now = Date.now(), id = 0;
      function push(t, category, method, person, event, detail, photo) {
        if (t > now) return;
        rows.push({ uid: "demo" + (++id), ts: new Date(t).toISOString(), category: category,
                    method: method, person: person, event: event, detail: detail,
                    level: category === "denied" ? "WARNING" : "INFO",
                    photo: photo ? picture(category, t) : "" });
      }
      var hours = [6, 7, 7, 8, 12, 13, 14, 16, 17, 18, 18, 19, 19, 20, 21, 22];
      for (var back = 95; back >= 0; back--) {
        var day = startOfDay(now).getTime() - back * 86400000;
        var weekday = new Date(day).getDay();
        var visits = 3 + Math.floor(random() * 5) + (weekday === 5 ? 3 : 0);
        for (var i = 0; i < visits; i++) {
          var t = day + pick(hours) * 3600000 + Math.floor(random() * 3600000);
          var roll = random();
          if (roll < 0.48) {
            var who = random() < 0.7 ? "Khalid" : "Sara";
            push(t, "entry", "face", who, "lock.unlock", "5.0s (face " + who + " 0.7" + Math.floor(random() * 9) + ")", true);
          } else if (roll < 0.68) {
            push(t, "entry", "keypad", "", "lock.unlock", "5.0s (keypad)", true);
          } else if (roll < 0.83) {
            var finger = random() < 0.6 ? "Khalid" : "Ahmed";
            push(t, "entry", "fingerprint", finger, "lock.unlock", "5.0s (fingerprint " + finger + " slot 1)", true);
          } else if (roll < 0.89) {
            push(t - 600000, "remote", "temp_code", "", "tempcode.add", "#a1b2 valid 600s", false);
            push(t, "entry", "temp_code", "", "lock.unlock", "5.0s (temp code)", true);
          } else if (roll < 0.94) {
            push(t - 2000, "remote", "whatsapp", "", "bot.command", "open from …4821", false);
            push(t, "entry", "whatsapp", "", "lock.unlock", "5.0s (whatsapp)", true);
          } else {
            var wait = 8000 + Math.floor(random() * 50000);
            push(t - wait, "doorbell", "doorbell", "", "doorbell.ring", "bell pressed on the door screen", true);
            push(t, "entry", "doorbell", "", "lock.unlock", "5.0s (whatsapp doorbell)", true);
            push(t + 300, "doorbell", "doorbell", "", "doorbell.answer", "owner opened the door (…4821)", false);
          }
        }
        var refused = random() < 0.45 ? 1 + Math.floor(random() * 2) : 0;
        for (var j = 0; j < refused; j++) {
          var at = day + pick([1, 3, 9, 15, 19, 20, 23]) * 3600000 + Math.floor(random() * 3600000);
          var kind = random();
          if (kind < 0.5) {
            push(at, "denied", "keypad", "", "keypad.deny", "wrong passcode (2 attempt(s) left)", true);
            if (random() < 0.25) {
              push(at + 9000, "denied", "keypad", "", "keypad.deny", "wrong passcode (1 attempt(s) left)", true);
              push(at + 17000, "denied", "keypad", "", "keypad.lockout", "too many failures, locked out for 30s", true);
            }
          } else if (kind < 0.8) {
            push(at, "denied", "face", "", "face.deny", "unknown face (closest Khalid 0.21)", true);
          } else {
            push(at, "denied", "fingerprint", "", "fingerprint.deny", "search: no matching fingerprint found (0x09)", true);
          }
        }
        if (random() < 0.22) {
          var ring = day + pick([10, 11, 16, 17, 20]) * 3600000 + Math.floor(random() * 3600000);
          push(ring, "doorbell", "doorbell", "", "doorbell.ring", "bell pressed on the door screen", true);
          if (random() < 0.5) push(ring + 21000, "doorbell", "doorbell", "", "doorbell.answer", "owner declined (…4821)", false);
          else push(ring + 120000, "doorbell", "doorbell", "", "doorbell.timeout", "no answer within 120s", false);
        }
        if (back === 9) {
          var fire = day + 15 * 3600000 + 1260000;
          push(fire, "alarm", "fire", "", "hazard.fire", "FIRE DETECTED on GPIO5", true);
          push(fire + 200, "alarm", "fire", "", "lock.unlock", "10.0s (fire alarm)", false);
          push(fire + 48000, "alarm", "fire", "", "hazard.fire", "fire alarm over (quiet for 15s)", false);
        }
        if (back % 13 === 0) push(day + 6 * 3600000, "system", "", "", "app.start", "config=config.ini", false);
      }
      rows.sort(function (a, c) { return a.ts < c.ts ? 1 : -1; });
    }
    function all() { if (!rows) build(); return rows; }

    // Dashboard accounts, in memory, under the same rules as the database.
    var role = "admin", accounts = null, changes = null;
    function iso(t) { return new Date(t).toISOString(); }
    function people() {
      if (accounts) return accounts;
      var now = Date.now(), day = 86400000;
      accounts = [
        { user_id: "demo-khalid", email: "khalid@example.com", role: "admin",
          created_at: iso(now - 60 * day), last_sign_in_at: iso(now - 4 * 60000) },
        { user_id: "demo-sara", email: "sara@example.com", role: "member",
          created_at: iso(now - 21 * day), last_sign_in_at: iso(now - 26 * 3600000) },
        { user_id: "demo-ahmed", email: "ahmed@example.com", role: "member",
          created_at: iso(now - 9 * day), last_sign_in_at: null },
        { user_id: "demo-guard", email: "guard@example.com", role: null,
          created_at: iso(now - 2 * day), last_sign_in_at: null }
      ];
      changes = [
        { id: 3, ts: iso(now - 9 * day), actor_email: "khalid@example.com", action: "member.add",
          target_email: "ahmed@example.com", old_role: null, new_role: "member" },
        { id: 2, ts: iso(now - 20 * day), actor_email: "khalid@example.com", action: "password.reset",
          target_email: "sara@example.com", old_role: null, new_role: null },
        { id: 1, ts: iso(now - 21 * day), actor_email: "khalid@example.com", action: "member.add",
          target_email: "sara@example.com", old_role: null, new_role: "member" }
      ];
      return accounts;
    }
    function self() {
      var email = role === "admin" ? "khalid@example.com" : "sara@example.com";
      return people().filter(function (a) { return a.email === email; })[0];
    }
    function need(ok, code) { if (!ok) throw new Error(code); }
    function admin() { need(self().role === "admin", "not_admin"); }
    function find(id) {
      var hit = people().filter(function (a) { return a.user_id === id; })[0];
      need(hit, "no_user");
      return hit;
    }
    function note(action, target, before, after) {
      changes.unshift({ id: Date.now(), ts: iso(Date.now()), actor_email: self().email, action: action,
                        target_email: target.email, old_role: before, new_role: after });
    }
    function later(value) {          // a moment, like a request over the internet
      return new Promise(function (resolve) { setTimeout(function () { resolve(value); }, 200); });
    }

    return {
      useRole: function (next) { role = next === "member" ? "member" : "admin"; },
      me: async function () {
        var me = self();
        return { user_id: me.user_id, email: me.email, role: me.role };
      },
      events: async function (fromISO) {
        var who = self().role;
        return all().filter(function (r) { return r.ts >= fromISO && visibleTo(who, r); });
      },
      count: async function (category, fromISO, toISO) {
        var who = self().role;
        return all().filter(function (r) {
          return r.category === category && r.ts >= fromISO && r.ts < toISO && visibleTo(who, r);
        }).length;
      },
      status: async function () {
        return { id: "door", updated_at: new Date(Date.now() - 20000).toISOString(), lock: "locked",
                 alarm: "", camera: true, bot: true, info: {} };
      },
      sign: async function () { return {}; },
      subscribe: function () {},
      unsubscribe: function () {},
      signOut: async function () {},
      changePassword: async function (password) {
        need(String(password).length >= MIN_PASSWORD, "weak_password");
        return later();
      },
      members: async function () {
        admin();
        var me = self();
        return later(people().map(function (a) { return Object.assign({ is_me: a === me }, a); }));
      },
      auditLog: async function (limit) {
        admin();
        return changes.slice(0, limit);
      },
      setRole: async function (id, next) {
        admin();
        need(next === "admin" || next === "member", "bad_role");
        var target = find(id);
        need(target !== self(), "self_change");
        if (target.role !== next) {
          note(target.role ? "member.role" : "member.add", target, target.role, next);
          target.role = next;
        }
        return later();
      },
      createAccount: async function (email, password, next) {
        admin();
        email = String(email).trim().toLowerCase();
        need(EMAIL.test(email), "bad_email");
        need(String(password).length >= MIN_PASSWORD, "weak_password");
        need(next === "admin" || next === "member", "bad_role");
        need(!people().some(function (a) { return a.email === email; }), "email_exists");
        var account = { user_id: "demo-" + Date.now(), email: email, role: next,
                        created_at: iso(Date.now()), last_sign_in_at: null };
        accounts.push(account);
        note("member.add", account, null, next);
        return later({ ok: true });
      },
      deleteAccount: async function (id) {
        admin();
        var target = find(id);
        need(target !== self(), "self_change");
        if (target.role) note("member.remove", target, target.role, null);
        accounts.splice(accounts.indexOf(target), 1);
        return later({ ok: true });
      },
      resetPassword: async function (id, password) {
        admin();
        var target = find(id);
        need(String(password).length >= MIN_PASSWORD, "weak_password");
        note("password.reset", target, null, null);
        return later({ ok: true });
      }
    };
  })();

  // ------------------------------------------------------------------ loading
  function rangeStart() {
    var d = startOfDay(Date.now());
    d.setDate(d.getDate() - (RANGE_DAYS[state.range] - 1));
    return d;
  }

  function banner(text, isError) {
    var node = $("banner");
    node.hidden = !text;
    node.textContent = text || "";
    node.className = "banner" + (isError ? " error" : "");
  }

  function demoBanner() {
    var node = $("banner"), other = isAdmin() ? "member" : "admin";
    node.hidden = false;
    node.className = "banner";
    add(clear(node), ["عرض تجريبي ببيانات وهمية، بصلاحية «" + ROLE_NAME[state.role] + "». ",
                      el("a", { href: "?demo=" + other }, "اعرضها بصلاحية «" + ROLE_NAME[other] + "»")]);
  }

  async function load() {
    var source = state.source;
    if (!source) return;
    var content = $("content");
    content.classList.add("loading");    // the previous render stays, dimmed
    try {
      var from = rangeStart(), days = RANGE_DAYS[state.range];
      var before = new Date(from.getTime());
      before.setDate(before.getDate() - days);
      var fromISO = from.toISOString(), beforeISO = before.toISOString();
      // The role is asked again on every load: a change made by an admin
      // reaches an open page within a minute.
      var got = await Promise.all([source.events(fromISO), source.status(),
                                   source.count("entry", beforeISO, fromISO),
                                   source.count("denied", beforeISO, fromISO), source.me()]);
      if (state.source !== source) return;            // signed out meanwhile
      var me = got[4];
      if (!me || !me.role) {
        showGate("denied", "", me ? me.email : "");
        return;
      }
      if (me.role !== state.role) {
        applyRole(me);
        toast("صلاحيتك الآن: " + ROLE_NAME[me.role]);
      }
      got[0].forEach(function (r) { r.t = Date.parse(r.ts); });
      state.rows = got[0];
      state.status = got[1];
      state.prev = { entry: got[2], denied: got[3] };
      state.loadedAt = Date.now();
      if (state.demo) demoBanner();
      else banner("", false);
      render();
      if (isAdmin()) loadAccounts();
    } catch (err) {
      if (state.source !== source) return;
      banner(errorText(err), true);
      if (/JWT|expired|401|no_session/i.test(String(err && err.message))) showGate("login", errorText(err));
    } finally {
      content.classList.remove("loading");
    }
  }

  var pending = 0;
  function renderSoon() {
    clearTimeout(pending);
    pending = setTimeout(render, 400);
  }

  function onLiveEvent(row) {
    if (!row || !row.uid || !row.ts) return;
    row.t = Date.parse(row.ts);
    if (row.t < rangeStart().getTime()) return;
    if (state.rows.some(function (r) { return r.uid === row.uid; })) return;
    state.rows.push(row);
    state.rows.sort(function (a, c) { return c.t - a.t; });
    renderSoon();
  }

  // ---------------------------------------------------------------- rendering
  function render() {
    var rows = state.rows;
    var entries = rows.filter(function (r) { return r.category === "entry"; });
    var denied = rows.filter(function (r) { return r.category === "denied" && r.method !== "whatsapp"; });
    var rings = rows.filter(function (r) { return r.event === "doorbell.ring"; });
    var alarms = rows.filter(function (r) { return r.category === "alarm" && /DETECTED/.test(r.detail || ""); });
    renderDevice();
    renderKpis(entries, denied, rings, alarms, rows);
    renderActivity(entries);
    renderHours(entries);
    ranked($("methods"), tally(entries, function (r) { return METHOD[r.method] || r.method || "أخرى"; }),
           "دخول", "لا يوجد دخول في هذه الفترة");
    ranked($("people"), tally(entries, function (r) { return r.person; }),
           "دخول", "لا يوجد دخول باسم معروف في هذه الفترة");
    renderDenied(denied);
    renderInsights(entries, denied, rings, alarms, rows);
    if (isAdmin()) renderGallery();
    renderLog();
    $("range-note").textContent = state.range === "today" ? F_WEEKDAY.format(Date.now())
      : "من " + F_DAY.format(rangeStart()) + " إلى اليوم";
    $("foot-note").textContent = "آخر تحميل " + F_TIME.format(state.loadedAt) + " · " +
      n(rows.length) + " حدث في الفترة" + (state.demo ? " · بيانات تجريبية" : "");
  }

  function renderDevice() {
    var host = clear($("device")), status = state.status;
    if (!status) {
      host.appendChild(el("span", { "class": "chip" }, el("i", { "class": "dot" }),
                          "لم يصل أي تحديث من جهاز الباب بعد"));
      return;
    }
    var age = Date.now() - Date.parse(status.updated_at);
    var online = age < ONLINE_WITHIN;
    host.appendChild(el("span", { "class": "chip" },
      el("i", { "class": "dot " + (online ? "good" : "critical") }),
      b(online ? "الجهاز متصل" : "الجهاز غير متصل"),
      (online ? "آخر تحديث " : "آخر ظهور ") + ago(age)));
    if (!online) return;                 // an old snapshot would only mislead
    if (status.alarm) {
      host.appendChild(el("span", { "class": "chip" }, icon("i-flame", "status-critical"),
        b("إنذار " + status.alarm.split("+").map(function (k) { return METHOD[k] || k; }).join(" و") + " الآن")));
    }
    host.appendChild(el("span", { "class": "chip" }, icon("i-lock"), "القفل",
                        b(status.lock === "unlocked" ? "مفتوح" : "مقفل")));
    if (status.camera !== null && status.camera !== undefined) {
      host.appendChild(el("span", { "class": "chip" },
        icon("i-camera", status.camera ? "" : "status-critical"), "الكاميرا",
        b(status.camera ? "تعمل" : "متوقفة")));
    }
    if (status.bot !== null && status.bot !== undefined) {
      host.appendChild(el("span", { "class": "chip" },
        icon("i-chat", status.bot ? "" : "status-warning"), "بوت واتساب",
        b(status.bot ? "متصل" : "غير متصل")));
    }
  }

  function kpi(iconName, label, value, note, noteClass, noteIcon, isText) {
    return el("div", { "class": "kpi" },
      el("div", { "class": "kpi-label" }, icon(iconName), label),
      el("div", { "class": "kpi-value" + (isText ? " text" : "") }, value),
      el("div", { "class": "kpi-note" + (noteClass ? " " + noteClass : "") },
         noteIcon ? icon(noteIcon) : null, note || ""));
  }

  function renderKpis(entries, denied, rings, alarms, rows) {
    var host = clear($("kpis")), prev = state.prev;
    var than = state.range === "today" ? "عن أمس" : "عن الفترة السابقة";
    function delta(now, before, upIsBad) {
      if (before === null || before === undefined) return ["", "", null];
      var diff = now - before;
      if (!diff) return ["مثل " + (state.range === "today" ? "أمس" : "الفترة السابقة"), "", null];
      var text = (diff > 0 ? "أكثر بـ " : "أقل بـ ") + n(Math.abs(diff)) + " " + than;
      var cls = upIsBad === null ? "" : (diff > 0) === upIsBad ? "bad" : "good";
      return [text, cls, diff > 0 ? "i-up" : "i-down"];
    }
    var d1 = delta(entries.length, prev.entry, null);
    host.appendChild(kpi("i-door", "الدخول", n(entries.length), d1[0], d1[1], d1[2]));
    var d2 = delta(denied.length, prev.denied, true);
    host.appendChild(kpi("i-ban", "محاولات مرفوضة", n(denied.length), d2[0], d2[1], d2[2]));
    var opened = rows.filter(function (r) { return r.event === "doorbell.answer" && /opened/.test(r.detail || ""); }).length;
    host.appendChild(kpi("i-bell", "رنّات الجرس", n(rings.length),
                         rings.length ? "فُتح الباب في " + n(opened) + " منها" : ""));
    host.appendChild(kpi("i-flame", "إنذارات", n(alarms.length),
                         alarms.length ? "آخرها " + when(alarms[0].t) : "لا إنذارات في الفترة"));
    var last = entries[0];
    host.appendChild(last
      ? kpi("i-clock", "آخر دخول", (last.person ? last.person + " · " : "") + (METHOD[last.method] || last.method),
            when(last.t), "", null, true)
      : kpi("i-clock", "آخر دخول", "لا يوجد", "في هذه الفترة", "", null, true));
  }

  function activityBuckets(rows, valuesOf, width) {
    var buckets = [], i, t;
    if (state.range === "today") {
      for (i = 0; i < 24; i++) {
        buckets.push({ label: String(i), title: "اليوم " + hourLabel(i) + " – " + hourLabel((i + 1) % 24),
                       values: new Array(width).fill(0) });
      }
      rows.forEach(function (r) { valuesOf(r, buckets[new Date(r.t).getHours()].values); });
      return buckets;
    }
    var start = rangeStart().getTime(), days = RANGE_DAYS[state.range], index = new Map();
    for (i = 0; i < days; i++) {
      var d = new Date(start);
      d.setDate(d.getDate() + i);
      t = d.getTime();
      index.set(t, i);
      buckets.push({ label: d.getDate() + "/" + (d.getMonth() + 1), title: F_WEEKDAY.format(d),
                     values: new Array(width).fill(0) });
    }
    rows.forEach(function (r) {
      var at = index.get(startOfDay(r.t).getTime());
      if (at !== undefined) valuesOf(r, buckets[at].values);
    });
    return buckets;
  }

  function renderActivity(entries) {
    var today = state.range === "today";
    $("activity-title").textContent = today ? "الدخول اليوم، ساعة بساعة" : "الدخول اليومي";
    var buckets = activityBuckets(entries, function (r, values) {
      var group = GROUP_OF[r.method] || "other";
      values[GROUPS.findIndex(function (g) { return g.key === group; })]++;
    }, GROUPS.length);
    var legend = clear($("activity-legend"));
    GROUPS.forEach(function (group) {
      legend.appendChild(el("span", null, el("i", { "class": "swatch", style: "background:" + group.color }), group.name));
    });
    var opts = { buckets: buckets, series: GROUPS, anchor: today ? "start" : "end",
                 summary: "الدخول: " + n(entries.length) + " في الفترة المختارة. استخدم السهمين للتنقل بين الأعمدة." };
    columns($("activity-chart"), opts);
    chartTable($("activity-table"), today ? "الساعة" : "اليوم", opts);
  }

  function renderHours(entries) {
    $("card-hours").hidden = state.range === "today";   // the main chart is already hourly
    if (state.range === "today") return;
    var buckets = [];
    for (var h = 0; h < 24; h++) {
      buckets.push({ label: String(h), title: hourLabel(h) + " – " + hourLabel((h + 1) % 24), values: [0] });
    }
    entries.forEach(function (r) { buckets[new Date(r.t).getHours()].values[0]++; });
    var opts = { buckets: buckets, series: [{ name: "دخول", color: "var(--series-1)" }], unit: "دخول",
                 anchor: "start", summary: "عدد مرات الدخول في كل ساعة من اليوم." };
    columns($("hours-chart"), opts);
    chartTable($("hours-table"), "الساعة", opts);
  }

  function renderDenied(denied) {
    var today = state.range === "today";
    $("denied-title").textContent = today ? "المحاولات المرفوضة اليوم، ساعة بساعة" : "المحاولات المرفوضة يوميًا";
    var buckets = activityBuckets(denied, function (r, values) { values[0]++; }, 1);
    var opts = { buckets: buckets, series: [{ name: "محاولات مرفوضة", color: "var(--critical)" }],
                 unit: "محاولة مرفوضة", anchor: today ? "start" : "end",
                 empty: "لا توجد محاولات مرفوضة في هذه الفترة",
                 summary: "المحاولات المرفوضة: " + n(denied.length) + " في الفترة المختارة." };
    columns($("denied-chart"), opts);
    chartTable($("denied-table"), today ? "الساعة" : "اليوم", opts);
  }

  function renderInsights(entries, denied, rings, alarms, rows) {
    var host = clear($("insights"));
    function line(iconName, cls) {
      host.appendChild(add(el("li", null, icon(iconName, cls)),
                           [add(el("span"), Array.prototype.slice.call(arguments, 2))]));
    }
    if (!rows.length) {
      line("i-clock", "", "لا توجد أحداث مسجّلة في هذه الفترة.");
      return;
    }
    var days = RANGE_DAYS[state.range];
    if (entries.length) {
      if (days > 1) {
        line("i-door", "", b(n(entries.length)), " دخول في الفترة، بمعدل ",
             b((entries.length / days).toFixed(1)), " في اليوم.");
      } else {
        line("i-door", "", b(n(entries.length)), " دخول اليوم حتى الآن.");
      }
      var byHour = new Array(24).fill(0);
      entries.forEach(function (r) { byHour[new Date(r.t).getHours()]++; });
      var best = byHour.indexOf(Math.max.apply(null, byHour));
      line("i-clock", "", "أكثر وقت دخول بين ", b(hourLabel(best)), " و", b(hourLabel((best + 1) % 24)),
           " (", times(byHour[best]), ").");
      var methods = tally(entries, function (r) { return METHOD[r.method] || r.method || "أخرى"; });
      line("i-key", "", "الطريقة الأكثر استخدامًا: ", b(methods[0].name), " بنسبة ",
           b(Math.round(methods[0].value / entries.length * 100) + "%"), ".");
      var people = tally(entries, function (r) { return r.person; });
      if (people.length) line("i-user", "", "الأكثر دخولًا: ", b(people[0].name), " (", times(people[0].value), ").");
      var night = entries.filter(function (r) { return new Date(r.t).getHours() < 5; }).length;
      if (night) line("i-alert", "status-warning", b(n(night)), " دخول بين منتصف الليل والخامسة فجرًا.");
      var remote = entries.filter(function (r) { return r.method === "whatsapp"; }).length;
      if (remote) line("i-chat", "", "فُتح الباب من واتساب ", b(times(remote)), ".");
      var guests = entries.filter(function (r) { return r.method === "temp_code"; }).length;
      if (guests) line("i-key", "", "استُخدم رمز مؤقت للضيوف ", b(times(guests)), ".");
    } else {
      line("i-door", "", "لا يوجد دخول في هذه الفترة.");
    }
    if (denied.length) {
      var kinds = tally(denied, function (r) { return METHOD[r.method] || r.method; })
        .map(function (item) { return item.name + " " + n(item.value); }).join("، ");
      line("i-ban", "status-critical", b(n(denied.length)), " محاولة مرفوضة (", kinds, ").");
      var lockouts = denied.filter(function (r) { return r.event === "keypad.lockout"; }).length;
      if (lockouts) line("i-lock", "status-critical", "قُفلت لوحة الأرقام ", b(times(lockouts)), " بسبب تكرار الرمز الخاطئ.");
    } else {
      line("i-check", "status-good", "لا توجد محاولات مرفوضة في هذه الفترة.");
    }
    if (rings.length) {
      var answers = rows.filter(function (r) { return r.event === "doorbell.answer"; });
      var opened = answers.filter(function (r) { return /opened/.test(r.detail || ""); }).length;
      var declined = answers.filter(function (r) { return /declined/.test(r.detail || ""); }).length;
      var silent = rows.filter(function (r) { return r.event === "doorbell.timeout"; }).length;
      line("i-bell", "", "رنّ الجرس ", b(times(rings.length)), ": فُتح الباب ", b(n(opened)),
           "، رفض ", b(n(declined)), "، بدون رد ", b(n(silent)), ".");
      var waits = [];
      answers.forEach(function (answer) {
        var ring = rings.find(function (r) { return r.t <= answer.t && answer.t - r.t < 600000; });
        if (ring) waits.push((answer.t - ring.t) / 1000);
      });
      if (waits.length) line("i-clock", "", "متوسط وقت رد المالك على الجرس: ", b(seconds(sum(waits) / waits.length)), ".");
    }
    if (alarms.length) {
      line("i-flame", "status-critical", b(n(alarms.length)), " إنذار في الفترة، آخرها ", b(when(alarms[0].t)), ".");
    }
  }

  // ------------------------------------------------------------------ gallery
  function tabs(host, current, choose, extra) {
    clear(host);
    TABS.concat(extra || []).forEach(function (tab) {
      host.appendChild(el("button", { type: "button", "aria-pressed": String(tab[0] === current),
                                      onclick: function () { choose(tab[0]); } }, tab[1]));
    });
  }

  function photoRows() {
    return state.rows.filter(function (r) {
      return r.photo && (state.galleryTab === "all" || r.category === state.galleryTab);
    });
  }

  /** Signed links for bucket paths, fetched in one request and reused until they expire. */
  async function resolvePhotos(rows) {
    var need = [];
    rows.forEach(function (r) {
      if (r.photo.slice(0, 5) === "data:") return;
      var hit = signed.get(r.photo);
      if ((!hit || hit.until < Date.now()) && need.indexOf(r.photo) < 0) need.push(r.photo);
    });
    if (need.length) {
      var map = await state.source.sign(need);
      Object.keys(map).forEach(function (path) {
        signed.set(path, { url: map[path], until: Date.now() + (SIGN_SECONDS - 300) * 1000 });
      });
    }
  }

  function photoUrl(r) {
    if (r.photo.slice(0, 5) === "data:") return r.photo;
    var hit = signed.get(r.photo);
    return hit ? hit.url : "";
  }

  function renderGallery() {
    tabs($("gallery-tabs"), state.galleryTab, function (tab) {
      state.galleryTab = tab;
      state.galleryShown = GALLERY_STEP;
      renderGallery();
    });
    var all = photoRows(), shown = all.slice(0, state.galleryShown);
    var host = $("gallery");
    var signature = shown.map(function (r) { return r.uid; }).join(",");
    $("gallery-more").hidden = all.length <= shown.length;
    if (host._signature === signature) return;     // same pictures: leave the images alone
    host._signature = signature;
    clear(host);
    if (!shown.length) {
      host.appendChild(el("p", { "class": "empty" }, "لا توجد صور في هذه الفترة"));
      return;
    }
    var images = [];
    shown.forEach(function (r, i) {
      var image = el("img", { alt: describe(r) + "، " + when(r.t), loading: "lazy", decoding: "async" });
      images.push(image);
      host.appendChild(el("button", { "class": "shot", type: "button",
                                      onclick: function () { openViewer(all, i); } },
        image,
        el("span", { "class": "shot-text" },
          el("span", { "class": "shot-what" }, tag(r), r.person || ""),
          el("span", { "class": "shot-when" }, describe(r) + " · " + when(r.t)))));
    });
    resolvePhotos(shown).then(function () {
      if (host._signature !== signature) return;
      shown.forEach(function (r, i) { var url = photoUrl(r); if (url) images[i].src = url; });
    }).catch(function (err) { banner(errorText(err), true); });
  }

  // ------------------------------------------------------------------- viewer
  function openViewer(rows, at) {
    state.viewer = rows;
    state.viewerAt = at;
    showViewer();
    var dialog = $("viewer");
    if (!dialog.open) dialog.showModal();
  }

  function showViewer() {
    var r = state.viewer[state.viewerAt];
    if (!r) return;
    var image = $("viewer-img");
    image.removeAttribute("src");
    image.alt = describe(r) + "، " + when(r.t);
    resolvePhotos([r]).then(function () {
      if (state.viewer[state.viewerAt] === r) image.src = photoUrl(r);
    }).catch(function (err) { banner(errorText(err), true); });
    var info = clear($("viewer-info"));
    info.appendChild(tag(r));
    info.appendChild(el("dl", null,
      el("dt", null, "الحدث"), el("dd", null, describe(r)),
      r.person ? [el("dt", null, "الشخص"), el("dd", null, r.person)] : null,
      el("dt", null, "الوقت"), el("dd", null, F_FULL.format(r.t)),
      el("dt", null, "السجل"), el("dd", { dir: "ltr" }, r.event + " " + (r.detail || ""))));
    $("viewer-newer").disabled = state.viewerAt <= 0;
    $("viewer-older").disabled = state.viewerAt >= state.viewer.length - 1;
  }

  function stepViewer(by) {
    var next = state.viewerAt + by;
    if (next < 0 || next >= state.viewer.length) return;
    state.viewerAt = next;
    showViewer();
  }

  // ---------------------------------------------------------------------- log
  function logRows() {
    var needle = state.search.trim().toLowerCase();
    return state.rows.filter(function (r) {
      if (state.logTab === "other") {
        if (["remote", "admin", "system"].indexOf(r.category) < 0) return false;
      } else if (state.logTab !== "all" && r.category !== state.logTab) {
        return false;
      }
      if (!needle) return true;
      return [describe(r), r.person, METHOD[r.method], CATEGORY[r.category], r.event, r.detail]
        .join(" ").toLowerCase().indexOf(needle) >= 0;
    });
  }

  function renderLog() {
    if (!isAdmin() && state.logTab === "other") state.logTab = "all";
    tabs($("log-tabs"), state.logTab, function (tab) {
      state.logTab = tab;
      state.logPage = 0;
      renderLog();
    }, isAdmin() ? [["other", "أخرى"]] : []);
    var all = logRows(), pages = Math.max(1, Math.ceil(all.length / LOG_STEP));
    state.logPage = Math.min(state.logPage, pages - 1);
    var shown = all.slice(state.logPage * LOG_STEP, (state.logPage + 1) * LOG_STEP);
    var body = clear($("log-body"));
    if (!shown.length) {
      body.appendChild(el("tr", null, el("td", { colspan: isAdmin() ? "5" : "4", "class": "empty" },
                                         "لا توجد أحداث مطابقة")));
    }
    var withPhotos = state.rows.filter(function (r) { return r.photo; });
    shown.forEach(function (r) {
      body.appendChild(el("tr", null,
        el("td", { "class": "when" }, when(r.t)),
        el("td", null, tag(r)),
        el("td", null, describe(r), el("small", null, r.detail || "")),
        el("td", { "class": "who" }, r.person || "—"),
        isAdmin() ? el("td", null, r.photo ? el("button", { "class": "link", type: "button",
          onclick: function () { openViewer(withPhotos, withPhotos.indexOf(r)); } },
          icon("i-camera"), "عرض") : "") : null));
    });
    $("log-count").textContent = count(all.length, "حدث واحد", "حدثان", "أحداث", "حدثًا");
    $("log-page").textContent = "صفحة " + n(state.logPage + 1) + " من " + n(pages);
    $("log-prev").disabled = state.logPage <= 0;
    $("log-next").disabled = state.logPage >= pages - 1;
  }

  // ------------------------------------------------------------------- toast
  var toastTimer = 0;
  function toast(text) {
    var node = $("toast");
    node.textContent = text;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { node.hidden = true; }, 5000);
  }

  // ------------------------------------------------------------------- sheet
  // One small modal form, reused for: add an account, a new password, a
  // confirmation, "my account".
  //   job = { title, body: [nodes], submit, danger, onSubmit() -> message, onClose() }
  var sheetJob = null, fieldSeq = 0;

  function openSheet(job) {
    var dialog = $("sheet"), body = $("sheet-body"), submit = $("sheet-submit");
    sheetJob = job;
    $("sheet-title").textContent = job.title;
    add(clear(body), job.body || []);
    submit.hidden = !job.onSubmit;
    submit.disabled = false;
    submit.textContent = job.submit || "حفظ";
    submit.className = "button " + (job.danger ? "danger" : "primary");
    $("sheet-cancel").textContent = job.onSubmit ? "إلغاء" : "إغلاق";
    sheetError("");
    if (!dialog.open) dialog.showModal();
    var first = body.querySelector("input:not([type=radio]), select");
    if (first) first.focus();
  }

  function sheetError(text) {
    var node = $("sheet-error");
    node.hidden = !text;
    node.textContent = text || "";
  }

  function closeSheet() {
    var job = sheetJob, dialog = $("sheet");
    sheetJob = null;
    if (dialog.open) dialog.close();
    if (job && job.onClose) job.onClose();
  }

  function bindSheet() {
    var dialog = $("sheet");
    $("sheet-form").addEventListener("submit", async function (event) {
      event.preventDefault();
      var job = sheetJob;
      if (!job || !job.onSubmit) { closeSheet(); return; }
      var button = $("sheet-submit");
      button.disabled = true;
      sheetError("");
      try {
        var done = await job.onSubmit();
        if (sheetJob === job) closeSheet();
        if (done) toast(done);
      } catch (err) {
        if (sheetJob === job) sheetError(errorText(err));
      } finally {
        button.disabled = false;
      }
    });
    $("sheet-cancel").addEventListener("click", closeSheet);
    $("sheet-x").addEventListener("click", closeSheet);
    // Escape closes the dialog without closeSheet(): tidy up here.  A dialog
    // that is open again by now already belongs to the next job.
    dialog.addEventListener("close", function () {
      if (dialog.open || !sheetJob) return;
      var job = sheetJob;
      sheetJob = null;
      if (job.onClose) job.onClose();
    });
  }

  /** A label above an input, an optional button beside it and a hint below. */
  function labelled(text, input, hint, beside) {
    if (!input.id) input.id = "field-" + (++fieldSeq);
    return el("div", { "class": "field" },
      el("label", { "for": input.id }, text),
      beside ? el("div", { "class": "input-row" }, input, beside) : input,
      hint ? el("small", { "class": "muted" }, hint) : null);
  }

  function textField(type, autocomplete) {
    return el("input", { type: type, "class": "input", dir: "ltr", autocomplete: autocomplete,
                         spellcheck: "false", autocapitalize: "off" });
  }

  /** A readable password with a "make one" button: the admin hands it over. */
  function newPasswordField(text, hint) {
    var input = textField("text", "off");
    var make = el("button", { type: "button", "class": "button",
      onclick: function () { input.value = newPassword(); input.focus(); input.select(); } },
      icon("i-refresh"), "توليد");
    return { input: input, node: labelled(text, input, hint, make) };
  }

  /** 12 characters, at least one lower-case, upper-case and digit; no look-alikes. */
  function newPassword() {
    var sets = ["abcdefghijkmnpqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789"];
    var letters = sets.join(""), out = "";
    while (!sets.every(function (set) {
      return out.split("").some(function (c) { return set.indexOf(c) >= 0; });
    })) {
      var bytes = new Uint32Array(12);
      crypto.getRandomValues(bytes);
      out = Array.prototype.map.call(bytes, function (b) { return letters.charAt(b % letters.length); }).join("");
    }
    return out;
  }

  function checkPassword(value) {
    if (typeof value !== "string" || value.length < MIN_PASSWORD) throw new Error("weak_password");
    if (new TextEncoder().encode(value).length > MAX_PASSWORD_BYTES) throw new Error("long_password");
  }

  function roleBadge(role) {
    return el("span", { "class": "role-chip " + (role || "none") },
      icon(role === "admin" ? "i-shield" : role ? "i-user" : "i-ban"),
      role ? ROLE_NAME[role] : "بدون صلاحية");
  }

  function permissionList(role) {
    var column = role === "admin" ? 1 : 2;
    return el("ul", { "class": "perms" }, PERMISSIONS.map(function (p) {
      var yes = p[column];
      return el("li", { "class": yes ? "yes" : "no" }, icon(yes ? "i-check" : "i-close"),
                el("span", null, p[0]), el("span", { "class": "sr" }, yes ? " (مسموح)" : " (غير مسموح)"));
    }));
  }

  function rolesTable() {
    function mark(yes) {
      return el("td", { "class": yes ? "yes" : "no" }, icon(yes ? "i-check" : "i-close"),
                el("span", { "class": "sr" }, yes ? "نعم" : "لا"));
    }
    return el("table", { "class": "roles" },
      el("thead", null, el("tr", null, el("th", null, el("span", { "class": "sr" }, "الإجراء")),
                                       el("th", null, "مدير"), el("th", null, "مستخدم"))),
      el("tbody", null, PERMISSIONS.map(function (p) {
        return el("tr", null, el("th", { scope: "row" }, p[0]), mark(p[1]), mark(p[2]));
      })));
  }

  // ---------------------------------------------------------------- accounts
  async function loadAccounts() {
    var source = state.source;
    if (!source || !isAdmin()) return;
    var problem = "";
    try {
      var got = await Promise.all([source.members(), source.auditLog(AUDIT_ROWS)]);
      if (state.source !== source || !isAdmin()) return;
      state.accounts = got[0];
      state.audit = got[1];
    } catch (err) {
      if (state.source !== source) return;
      problem = errorText(err);
    }
    var note = $("accounts-error");
    note.hidden = !problem;
    note.textContent = problem;
    renderAccounts();
    renderAudit();
  }

  function renderAccounts() {
    var body = clear($("accounts-body")), list = state.accounts;
    if (!list) {
      body.appendChild(el("tr", null, el("td", { colspan: "4", "class": "empty" }, "جارٍ التحميل…")));
      return;
    }
    var admins = list.filter(function (a) { return a.role === "admin"; }).length;
    var members = list.filter(function (a) { return a.role === "member"; }).length;
    var none = list.length - admins - members;
    $("accounts-count").textContent = count(list.length, "حساب واحد", "حسابان", "حسابات", "حسابًا") +
      ": مدير " + n(admins) + " · مستخدم " + n(members) + (none ? " · بدون صلاحية " + n(none) : "");
    list.forEach(function (a) {
      body.appendChild(el("tr", null,
        el("td", null, el("bdi", { "class": "email" }, a.email),
           a.is_me ? el("span", { "class": "you" }, "أنت") : null),
        el("td", null, a.is_me ? roleBadge(a.role) : roleSelect(a)),
        el("td", { "class": "when who" }, a.last_sign_in_at ? when(Date.parse(a.last_sign_in_at))
                                                            : el("span", { "class": "muted" }, "لم يدخل بعد")),
        el("td", null, a.is_me ? el("span", { "class": "muted" }, "من «حسابي»")
          : el("span", { "class": "row-actions" },
              el("button", { "class": "link", type: "button", onclick: function () { passwordSheet(a); } },
                 icon("i-key"), "كلمة مرور"),
              el("button", { "class": "link danger", type: "button", onclick: function () { deleteSheet(a); } },
                 icon("i-trash"), "حذف")))));
    });
  }

  function roleSelect(account) {
    var select = el("select", { "class": "role-select", "aria-label": "صلاحية " + account.email },
      account.role ? null : el("option", { value: "", selected: true, disabled: true }, "بدون صلاحية"),
      el("option", { value: "member", selected: account.role === "member" }, ROLE_NAME.member),
      el("option", { value: "admin", selected: account.role === "admin" }, ROLE_NAME.admin));
    select.addEventListener("change", function () { roleSheet(account, select.value); });
    return select;
  }

  function auditLine(a) {
    var who = el("bdi", null, a.actor_email || "—");
    var whom = el("bdi", null, a.target_email || "حساب محذوف");
    var was = ROLE_NAME[a.old_role] || a.old_role || "", now = ROLE_NAME[a.new_role] || a.new_role || "";
    switch (a.action) {
      case "member.add": return [who, " منح ", whom, " صلاحية «" + now + "»"];
      case "member.role": return [who, " غيّر صلاحية ", whom, " من «" + was + "» إلى «" + now + "»"];
      case "member.remove": return [who, " حذف حساب ", whom, " (كان «" + was + "»)"];
      case "password.reset": return [who, " وضع كلمة مرور جديدة لـ ", whom];
      default: return [who, " " + a.action + " ", whom];
    }
  }

  function renderAudit() {
    var host = clear($("audit")), list = state.audit || [];
    if (!list.length) {
      if (state.audit) host.appendChild(el("li", { "class": "empty" }, "لا توجد تغييرات بعد"));
      return;
    }
    list.forEach(function (a) {
      var t = Date.parse(a.ts);
      host.appendChild(el("li", null, icon(AUDIT_ICON[a.action] || "i-gear"),
        add(el("span"), auditLine(a)),
        el("time", { "class": "muted", datetime: a.ts, title: F_FULL.format(t) }, when(t))));
    });
  }

  function roleChoice(initial) {
    var name = "role-" + (++fieldSeq);
    function option(role, text) {
      return el("label", { "class": "choice" },
        el("input", { type: "radio", name: name, value: role, checked: role === initial }),
        el("span", null, el("b", null, ROLE_NAME[role] + " · " + ROLE_NOTE[role]), el("small", null, text)));
    }
    var node = el("fieldset", { "class": "choices" }, el("legend", null, "الصلاحية"),
      option("member", "حالة الباب والأرقام والسجل، بدون صور الكاميرا وسجل النظام وإدارة الحسابات."),
      option("admin", "كل شيء، ومنه صور الكاميرا وسجل النظام وإدارة الحسابات."));
    return {
      node: node,
      value: function () {
        var hit = node.querySelector("input:checked");
        return hit ? hit.value : initial;
      }
    };
  }

  function addAccountSheet() {
    var email = textField("email", "off");
    var password = newPasswordField("كلمة مرور مؤقتة",
      "انسخها وأعطها لصاحب الحساب؛ يستطيع تغييرها بعد الدخول من «حسابي».");
    var choice = roleChoice("member");
    password.input.value = newPassword();
    openSheet({
      title: "إضافة حساب",
      body: [labelled("البريد الإلكتروني", email), password.node, choice.node],
      submit: "إضافة الحساب",
      onSubmit: async function () {
        var address = email.value.trim().toLowerCase(), role = choice.value();
        if (!EMAIL.test(address)) throw new Error("bad_email");
        checkPassword(password.input.value);
        await state.source.createAccount(address, password.input.value, role);
        await loadAccounts();
        return "أُضيف " + isolate(address) + " بصلاحية «" + ROLE_NAME[role] + "»";
      }
    });
  }

  function roleSheet(account, next) {
    var grant = !account.role;
    openSheet({
      title: grant ? "منح صلاحية" : "تغيير الصلاحية",
      body: [
        el("p", null, grant ? "منح " : "تغيير صلاحية ", el("bdi", { "class": "email" }, account.email),
           grant ? " صلاحية «" + ROLE_NAME[next] + "»؟"
                 : " من «" + ROLE_NAME[account.role] + "» إلى «" + ROLE_NAME[next] + "»؟"),
        el("p", { "class": "muted" }, "بهذه الصلاحية:"),
        permissionList(next)
      ],
      submit: "تأكيد",
      onSubmit: async function () {
        await state.source.setRole(account.user_id, next);
        await loadAccounts();
        return "صلاحية " + isolate(account.email) + " الآن: " + ROLE_NAME[next];
      },
      onClose: renderAccounts            // a cancelled change puts the menu back
    });
  }

  function passwordSheet(account) {
    var password = newPasswordField("كلمة المرور الجديدة",
      "انسخها وأعطها لصاحب الحساب؛ يستطيع تغييرها من «حسابي».");
    password.input.value = newPassword();
    openSheet({
      title: "كلمة مرور جديدة",
      body: [el("p", null, "للحساب ", el("bdi", { "class": "email" }, account.email)), password.node],
      submit: "حفظ كلمة المرور",
      onSubmit: async function () {
        checkPassword(password.input.value);
        await state.source.resetPassword(account.user_id, password.input.value);
        loadAccounts();
        return "تغيّرت كلمة مرور " + isolate(account.email);
      }
    });
  }

  function deleteSheet(account) {
    openSheet({
      title: "حذف حساب",
      body: [
        el("p", null, "حذف الحساب ", el("bdi", { "class": "email" }, account.email), " نهائيًا؟"),
        el("p", { "class": "muted" }, "لن يستطيع الدخول إلى هذه اللوحة بعد الآن. سجل الباب وصوره لا تتأثر.")
      ],
      submit: "حذف الحساب",
      danger: true,
      onSubmit: async function () {
        await state.source.deleteAccount(account.user_id);
        await loadAccounts();
        return "حُذف الحساب " + isolate(account.email);
      }
    });
  }

  function accountSheet() {
    var me = state.me || {};
    var fresh = textField("password", "new-password"), again = textField("password", "new-password");
    openSheet({
      title: "حسابي",
      body: [
        el("div", { "class": "me" }, el("bdi", { "class": "email" }, me.email || ""), roleBadge(state.role)),
        el("p", { "class": "muted" }, "ما تستطيع فعله بهذه الصلاحية:"),
        permissionList(state.role),
        el("h3", { "class": "subhead" }, "تغيير كلمة المرور"),
        labelled("كلمة المرور الجديدة", fresh, "8 أحرف على الأقل."),
        labelled("أعد كتابتها", again)
      ],
      submit: "حفظ كلمة المرور",
      onSubmit: async function () {
        checkPassword(fresh.value);
        if (fresh.value !== again.value) throw new Error("mismatch");
        await state.source.changePassword(fresh.value);
        return "تغيّرت كلمة مرورك";
      }
    });
  }


  // ------------------------------------------------------------------ startup
  /** mode: setup | login | denied (signed in, but no role, or the database is not ready). */
  function showGate(mode, message, email) {
    timers.forEach(clearInterval);
    timers = [];
    if (state.source && state.source.unsubscribe) state.source.unsubscribe();
    state.source = null;
    state.me = state.role = state.accounts = state.audit = null;
    document.documentElement.removeAttribute("data-role");
    closeSheet();
    if ($("viewer").open) $("viewer").close();
    $("app").hidden = true;
    $("gate").hidden = false;
    $("gate-setup").hidden = mode !== "setup";
    $("gate-login").hidden = mode !== "login";
    $("gate-denied").hidden = mode !== "denied";
    var error = $("login-error");
    error.hidden = !message || mode !== "login";
    error.textContent = message || "";
    $("denied-email").textContent = email || "";
    $("denied-lead").hidden = $("denied-hint").hidden = !!message;
    $("denied-error").hidden = !message || mode !== "denied";
    $("denied-error").textContent = message || "";
  }

  /** Signed in (or demo): ask the database who this is before showing anything. */
  async function enter(source, demo, role) {
    if (demo) source.useRole(role);
    var me;
    try {
      me = await source.me();
    } catch (err) {
      if (/JWT|expired|401|no_session/i.test(String(err && err.message))) showGate("login", errorText(err));
      else showGate("denied", errorText(err));
      return;
    }
    if (!me) { showGate("login"); return; }
    if (!me.role) { showGate("denied", "", me.email); return; }
    start(source, demo, me);
  }

  async function signOut() {
    var source = state.source || Cloud;
    showGate("login");
    try { await source.signOut(); } catch (err) { /* signed out locally anyway */ }
  }

  function applyRole(me) {
    state.me = me;
    state.role = me.role;
    document.documentElement.setAttribute("data-role", me.role);
    var chip = $("role-chip");
    add(clear(chip), [icon(me.role === "admin" ? "i-shield" : "i-user"), ROLE_NAME[me.role]]);
    chip.className = "role-chip " + me.role;
    chip.title = ROLE_NOTE[me.role];
    chip.hidden = false;
    $("card-gallery").hidden = !isAdmin();
    $("card-accounts").hidden = !isAdmin();
    $("log-photo-head").hidden = !isAdmin();
    $("denied-note").textContent = isAdmin() ? "رمز خاطئ، بصمة أو وجه غير معروف، PIN خاطئ"
                                             : "رمز خاطئ، بصمة أو وجه غير معروف";
    if (!isAdmin()) {
      state.accounts = state.audit = null;
      if ($("viewer").open) $("viewer").close();
    }
  }

  function start(source, demo, me) {
    state.source = source;
    state.demo = !!demo;
    state.accounts = state.audit = null;
    applyRole(me);
    $("gate").hidden = true;
    $("app").hidden = false;
    $("logout").hidden = state.demo;
    $("subtitle").textContent = state.demo ? "لوحة المتابعة · عرض تجريبي" : "لوحة المتابعة";
    $("roles-table").replaceChildren(rolesTable());
    setRange(state.range);
    source.subscribe(onLiveEvent, function (status) { state.status = status; renderDevice(); },
                     function (live) { $("live").hidden = !live; });
    // Realtime may be blocked by a network: a slow poll keeps the page honest.
    timers.push(setInterval(load, 60000));
    timers.push(setInterval(renderDevice, 15000));
  }

  function setRange(range) {
    state.range = range;
    state.galleryShown = GALLERY_STEP;
    state.logPage = 0;
    Array.prototype.forEach.call($("ranges").children, function (button) {
      button.setAttribute("aria-pressed", String(button.getAttribute("data-range") === range));
    });
    load();
  }

  function applyTheme(theme) {
    if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
    else document.documentElement.removeAttribute("data-theme");
  }

  function toggleTheme() {
    var current = document.documentElement.getAttribute("data-theme") ||
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    var next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("door-theme", next); } catch (err) { /* private window */ }
  }

  function bind() {
    $("ranges").addEventListener("click", function (event) {
      var range = event.target.getAttribute && event.target.getAttribute("data-range");
      if (range) setRange(range);
    });
    $("refresh").addEventListener("click", load);
    $("theme").addEventListener("click", toggleTheme);
    $("logout").addEventListener("click", signOut);
    $("denied-logout").addEventListener("click", signOut);
    $("demo-button").addEventListener("click", function () { enter(Demo, true, "admin"); });
    $("demo-member").addEventListener("click", function () { enter(Demo, true, "member"); });
    $("account").addEventListener("click", accountSheet);
    $("account-add").addEventListener("click", addAccountSheet);
    bindSheet();
    $("gate-login").addEventListener("submit", async function (event) {
      event.preventDefault();
      var button = event.target.querySelector("button");
      button.disabled = true;
      try {
        await Cloud.signIn($("login-email").value.trim(), $("login-password").value);
        $("login-password").value = "";
        await enter(Cloud, false);
      } catch (err) {
        showGate("login", errorText(err));
      } finally {
        button.disabled = false;
      }
    });
    $("gallery-more").addEventListener("click", function () {
      state.galleryShown += GALLERY_STEP;
      renderGallery();
    });
    $("log-search").addEventListener("input", function (event) {
      state.search = event.target.value;
      state.logPage = 0;
      renderLog();
    });
    $("log-prev").addEventListener("click", function () { state.logPage--; renderLog(); });
    $("log-next").addEventListener("click", function () { state.logPage++; renderLog(); });
    var dialog = $("viewer");
    $("viewer-close").addEventListener("click", function () { dialog.close(); });
    $("viewer-newer").addEventListener("click", function () { stepViewer(-1); });
    $("viewer-older").addEventListener("click", function () { stepViewer(1); });
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener("keydown", function (event) {
      // Right-to-left page: the left arrow goes on to the next (older) picture.
      if (event.key === "ArrowLeft") stepViewer(1);
      if (event.key === "ArrowRight") stepViewer(-1);
    });
    window.addEventListener("scroll", hideTip, { passive: true });
  }

  async function boot() {
    var stored = null;
    try { stored = localStorage.getItem("door-theme"); } catch (err) { /* private window */ }
    var params = new URLSearchParams(window.location.search);
    applyTheme(params.get("theme") || stored);
    if (RANGE_DAYS[params.get("range")]) state.range = params.get("range");
    bind();
    if (params.has("demo")) { enter(Demo, true, params.get("demo") === "member" ? "member" : "admin"); return; }
    if (!CFG.url || !CFG.key || !window.supabase) { showGate("setup"); return; }
    try {
      Cloud.init();
      if (await Cloud.session()) await enter(Cloud, false);
      else showGate("login");
    } catch (err) {
      showGate("login", errorText(err));
    }
  }

  boot();
})();
