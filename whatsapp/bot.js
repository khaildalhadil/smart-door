// WhatsApp link for the smart lock.  Runs on the Pi, next to the door app:
// no server, no webhook, no AI tokens.
//
// It logs in to WhatsApp as a linked device (WhatsApp -> Settings -> Linked
// devices), like WhatsApp Web, and talks only to the door API on
// 127.0.0.1:8000 (app/api.py):
//
//   * a command (any text) -> POST /command -> the reply (and picture) goes back;
//   * every 2 s GET /alerts -> doorbell, wrong code, fire ... are sent, then
//     acknowledged.  Polling also tells the door the bot is alive (the BOT OFF
//     badge disappears).
//
// Who it listens to:
//
//   * the "message yourself" chat of the linked account -- with your personal
//     number that is where you type "افتح الباب";
//   * direct chats from the numbers in config.ini [bot] owners -- for a
//     separate number linked for the door.
//
// Every other chat is ignored and nothing from it is logged or stored.  The
// session is in data/whatsapp-auth/ (mode 0700): whoever copies that folder is
// logged in to this WhatsApp account, so it is never committed.
//
// Languages: the door writes every reply and alert in Arabic and in English
// ("texts"); config.ini [bot] languages says which ones go out and in what
// order, each as its own message ("ar en", the default: Arabic, then English).
// A picture goes with the first one.
//
//   node bot.js            run (systemd: deploy/door-whatsapp.service)
//   node bot.js --link     link in the foreground: prints the QR in the terminal
//   node bot.js --pair 9689xxxxxxx   link with a pairing code instead of a QR
//   node bot.js --logout   unlink this device and delete the session

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import makeWASocket, {
  Browsers,
  DisconnectReason,
  fetchLatestBaileysVersion,
  fetchLatestWaWebVersion,
  isJidBroadcast,
  isJidGroup,
  isJidNewsletter,
  isLidUser,
  isPnUser,
  jidDecode,
  jidNormalizedUser,
  normalizeMessageContent,
  useMultiFileAuthState
} from 'baileys'
import pino from 'pino'
import QRCode from 'qrcode'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const DATA = path.join(ROOT, 'data')
const AUTH_DIR = path.join(DATA, 'whatsapp-auth')
const QR_PNG = path.join(DATA, 'whatsapp-qr.png')
const STATUS_FILE = path.join(DATA, 'whatsapp-status.json')

const ALERT_EVERY_MS = 2000
const STALE_SECONDS = 90          // older commands are not carried out
const TELL_STALE_SECONDS = 15 * 60
const LATE_ALERT_SECONDS = 300    // older alerts waited while WhatsApp was down
const SUMMARY_FROM = 3            // this many late alerts in a row -> one summary message
const SUMMARY_LINES = 15
const SENT_MEMORY = 500
const OFFLINE_BATCH = '100'       // held items asked for at a time (as Baileys does)
const OFFLINE_QUIET_MS = 3000     // nothing more for this long: that batch is in
const OFFLINE_EMPTY_ASKS = 10     // asked this often in a row with nothing sent: stop

const args = process.argv.slice(2)
// WA_DEBUG=1: one line per incoming message -- what kind, which chat type,
// why it was skipped -- never its text or anyone's number.
// WA_LOG_LEVEL=info (or debug): Baileys' own log, normally errors only.
const DEBUG = process.env.WA_DEBUG === '1'
const LINK = args.includes('--link')
const PAIR = args.includes('--pair') ? (args[args.indexOf('--pair') + 1] || '').replace(/\D/g, '') : ''
const LOGOUT = args.includes('--logout')

const log = (...parts) => console.log(new Date().toLocaleString('sv-SE'), ...parts)

// libsignal (inside Baileys) prints whole Signal sessions -- private keys
// included -- whenever one opens or closes ("Closing session: SessionEntry
// {...}").  They would end up in the journal: drop them.
const SIGNAL_NOISE = /^(Opening session|Closing session|Removing old closed session|Session already (open|closed)|Migrating session)/
for (const level of ['info', 'warn']) {
  const original = console[level].bind(console)
  console[level] = (first, ...rest) => {
    if (typeof first === 'string' && SIGNAL_NOISE.test(first)) return
    original(first, ...rest)
  }
}

// ------------------------------------------------------------------ settings
function readIni (file) {
  const out = {}
  let section = ''
  let text = ''
  try { text = fs.readFileSync(file, 'utf8') } catch { return out }
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#') || line.startsWith(';')) continue
    const head = line.match(/^\[(.+)\]$/)
    if (head) { section = head[1].trim(); out[section] = out[section] || {}; continue }
    const eq = line.indexOf('=')
    if (eq > 0 && section) out[section][line.slice(0, eq).trim()] = line.slice(eq + 1).trim()
  }
  return out
}

function envValue (name) {
  if (process.env[name]) return process.env[name].trim()
  let value = ''
  try {
    for (const line of fs.readFileSync(path.join(ROOT, '.env'), 'utf8').split(/\r?\n/)) {
      const eq = line.indexOf('=')
      if (eq > 0 && line.slice(0, eq).trim() === name) value = line.slice(eq + 1).trim().replace(/^['"]|['"]$/g, '')
    }
  } catch { /* no .env */ }
  return value
}

const LANGUAGES = ['ar', 'en']

/** [bot] languages ("ar en", "en,ar", "en" ...) as a list: known words only,
 *  each once, in the order given.  Nothing usable -> Arabic, then English. */
function languagesOf (raw) {
  const words = String(raw ?? '').toLowerCase().split(/[\s,;]+/).filter(w => LANGUAGES.includes(w))
  return words.length ? [...new Set(words)] : [...LANGUAGES]
}

// DOOR_API / DOOR_OWNERS / DOOR_LANGUAGES in the environment override
// config.ini (bench tests).
function settings () {
  const ini = readIni(path.join(ROOT, 'config.ini'))
  const raw = process.env.DOOR_OWNERS ?? ini.bot?.owners ?? ''
  const owners = raw.split(/[,;]/).map(o => o.replace(/\D/g, '')).filter(o => o.length >= 6)
  const port = Number(ini.api?.port || 8000) || 8000
  return {
    owners,
    door: process.env.DOOR_API || `http://127.0.0.1:${port}`,
    languages: languagesOf(process.env.DOOR_LANGUAGES ?? ini.bot?.languages)
  }
}

const DOOR_KEY = envValue('DOOR_KEY')

function writeStatus (state, extra = {}) {
  try {
    fs.mkdirSync(DATA, { recursive: true })
    fs.writeFileSync(STATUS_FILE, JSON.stringify({ state, time: new Date().toISOString(), ...extra }) + '\n', { mode: 0o600 })
  } catch (err) { log('status file:', err.message) }
}

// ------------------------------------------------------------------ door API
async function door (method, route, body, timeoutMs = 15000) {
  const { door: base } = settings()
  const res = await fetch(base + route, {
    method,
    headers: { 'X-Door-Key': DOOR_KEY, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(timeoutMs)
  })
  if (!res.ok) throw new Error(`door API ${method} ${route}: HTTP ${res.status}`)
  return res
}

async function photoBytes (route) {
  if (!route || !/^\/photos\/[\w.-]+\.jpg$/.test(route)) return null
  try {
    return Buffer.from(await (await door('GET', route)).arrayBuffer())
  } catch (err) {
    log('photo:', err.message)
    return null
  }
}

// ------------------------------------------------------------------ helpers
const userOf = jid => jidDecode(jid || '')?.user || ''

// Linked = WhatsApp gave this device an identity.  (creds.registered is only
// set by the pairing-code route, never by a QR scan.)
const linked = creds => Boolean(creds?.me?.id)

/** The version web.whatsapp.com serves today: WhatsApp refuses outdated
 *  clients ("check your connection" on the phone, or code 405). */
async function waVersion () {
  for (const latest of [fetchLatestWaWebVersion, fetchLatestBaileysVersion]) {
    try {
      const found = await latest()
      // On failure these return the built-in version with isLatest false.
      if (found?.version && found.isLatest !== false) return found.version
    } catch { /* try the next source, then the built-in one */ }
  }
  return undefined
}

function textOf (message) {
  const m = normalizeMessageContent(message)
  return (m?.conversation || m?.extendedTextMessage?.text || m?.buttonsResponseMessage?.selectedDisplayText ||
          m?.listResponseMessage?.title || m?.templateButtonReplyMessage?.selectedDisplayText || '').trim()
}

// ------------------------------------------------------------------ languages
// The bot's own messages, in every language it can send.
const STALE_COMMAND = {
  ar: '⏱ وصلتني رسالتك متأخرة (الباب كان غير متصل)، فما نفذتها. أرسلها مرة ثانية.',
  en: '⏱ Your message reached me late (the door was offline), so I did not carry it out. Send it again.'
}
const DOOR_SILENT = {
  ar: '⚠️ برنامج الباب لا يرد الآن. جرّب بعد قليل.',
  en: '⚠️ The door program is not answering right now. Try again in a moment.'
}
const LATE_ALERT = {
  ar: '⏱ تنبيه متأخر (كان الواتساب غير متصل):\n',
  en: '⏱ Late alert (WhatsApp was offline):\n'
}

/** ``texts`` ({ar, en}, from the door or the bot) in the [bot] languages, in
 *  that order, as [language, text] pairs; a text equal to one already taken
 *  goes once.  None of them (an alert queued before the door sent "texts"):
 *  the old single field, ``single``, in its language ``singleLang``. */
function inLanguages (texts, single = '', singleLang = '') {
  const out = []
  for (const lang of settings().languages) {
    const text = texts?.[lang]
    if (typeof text === 'string' && text.trim() && !out.some(([, t]) => t === text)) out.push([lang, text])
  }
  if (!out.length && typeof single === 'string' && single) out.push([singleLang, single])
  return out
}

const textsIn = (...args) => inLanguages(...args).map(([, text]) => text)

// ------------------------------------------------------------------ the bot
let sock = null
let connected = false
let pumping = false
let retry = 0
const sentIds = new Set()   // our own messages: never read back as commands

function remember (sent) {
  const id = sent?.key?.id
  if (!id) return
  sentIds.add(id)
  if (sentIds.size > SENT_MEMORY) sentIds.delete(sentIds.values().next().value)
}

function me () {
  const user = sock?.user || {}
  return { pn: userOf(user.id), lid: userOf(user.lid), jid: user.id ? jidNormalizedUser(user.id) : '' }
}

/** One reply or alert to one chat: its texts in order, each its own message;
 *  the picture (``image``, already fetched) goes with the first as caption. */
async function send (jid, texts, image = null) {
  for (const [i, text] of texts.entries()) {
    const sent = image && i === 0
      ? await sock.sendMessage(jid, { image, caption: text })
      : await sock.sendMessage(jid, { text })
    remember(sent)
  }
}

/** Phone number (digits) of whoever sent a direct message, or '' if unknown. */
async function senderNumber (key) {
  for (const jid of [key.remoteJidAlt, key.remoteJid]) {
    if (jid && isPnUser(jid)) return userOf(jid)
  }
  if (isLidUser(key.remoteJid)) {
    try {
      const pn = await sock.signalRepository.lidMapping.getPNForLID(key.remoteJid)
      if (pn) return userOf(pn)
    } catch { /* not known yet */ }
  }
  return ''
}

/** The chat this message came in, if it is one the door listens to. */
async function whoIsAsking (msg) {
  const key = msg.key || {}
  const jid = key.remoteJid || ''
  if (!jid || isJidGroup(jid) || isJidBroadcast(jid) || isJidNewsletter(jid)) return null
  const self = me()
  const chatUser = userOf(jid)
  if (key.fromMe) {
    // Only "message yourself"; what you write to other people is not ours.
    if (chatUser && (chatUser === self.pn || chatUser === self.lid)) return { jid, from: self.pn, selfChat: true }
    return null
  }
  const number = await senderNumber(key)
  if (number && settings().owners.includes(number)) return { jid, from: number, selfChat: false }
  return null
}

async function onMessage (msg) {
  if (!msg?.message || sentIds.has(msg.key?.id)) return
  const asker = await whoIsAsking(msg)
  if (!asker) return
  const text = textOf(msg.message)
  if (!text) return
  const age = Date.now() / 1000 - Number(msg.messageTimestamp || 0)
  if (age > STALE_SECONDS) {
    // Sent while the Pi or the internet was down: opening the door now, maybe
    // hours later, would surprise everyone.
    log(`skipped a command ${Math.round(age)} s old`)
    if (age < TELL_STALE_SECONDS) await send(asker.jid, textsIn(STALE_COMMAND))
    return
  }
  let result
  try {
    // Up to 90 s: a message the keywords don't know may go to OpenAI (app/ai.py).
    result = await (await door('POST', '/command', { text, from: asker.from, self: asker.selfChat }, 90000)).json()
  } catch (err) {
    log('command:', err.message)
    await send(asker.jid, textsIn(DOOR_SILENT))
    return
  }
  log(`command from ${asker.selfChat ? 'self chat' : '…' + asker.from.slice(-4)}: ${result.intent}`)
  // Notes to yourself that are not door commands get no answer.
  if (!result.handled && asker.selfChat) return
  const texts = textsIn(result.texts, result.reply)
  if (texts.length) await send(asker.jid, texts, await photoBytes(result.photo))
}

function alertTargets () {
  const self = me()
  const owners = settings().owners
  const list = owners.length ? owners : [self.pn]
  return [...new Set(list)].filter(Boolean).map(n => `${n}@s.whatsapp.net`)
}

const alertAge = alert => (Date.now() - Date.parse(alert.time || '')) / 1000

const shortTime = iso => {
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return ''
  const two = n => String(n).padStart(2, '0')
  return `${two(t.getDate())}/${two(t.getMonth() + 1)} ${two(t.getHours())}:${two(t.getMinutes())}`
}

/** One message for a pile of alerts that waited while WhatsApp was down
 *  (up to 50 after a long outage): the first line of each kind, counted.
 *  ``lang`` "ar" or "en"; an alert without that text (queued before the door
 *  sent "texts") is listed by its Arabic line. */
function summaryOf (late, lang = 'ar') {
  const en = lang === 'en'
  const counts = new Map()
  // "photo" = the picture that followed an alarm: the alarm itself is listed.
  const worth = late.some(a => a.event !== 'photo') ? late.filter(a => a.event !== 'photo') : late
  for (const alert of worth) {
    const own = alert.texts?.[lang]
    const text = typeof own === 'string' && own.trim() ? own : alert.message
    const line = String(text || '').split('\n')[0].trim()
    if (line) counts.set(line, (counts.get(line) || 0) + 1)
  }
  const lines = [...counts].slice(0, SUMMARY_LINES).map(([line, n]) => `• ${line}${n > 1 ? ` (×${n})` : ''}`)
  const more = counts.size - SUMMARY_LINES
  const from = shortTime(late[0].time)
  const to = shortTime(late[late.length - 1].time)
  if (en) {
    if (more > 0) lines.push(`• … and ${more} other kind${more === 1 ? '' : 's'}`)
    return `⏱ ${late.length} alert${late.length === 1 ? '' : 's'} came in while WhatsApp was offline` +
      (from && to ? ` (from ${from} to ${to})` : '') + ':\n' + lines.join('\n') +
      '\n\nThese are old, not happening now. New alerts reach you as they happen.'
  }
  if (more > 0) lines.push(`• … و${more} أنواع ثانية`)
  return `⏱ ${late.length} تنبيه صارت والواتساب غير متصل` + (from && to ? ` (من ${from} إلى ${to})` : '') +
    ':\n' + lines.join('\n') + '\n\nهذي قديمة، مو شي يصير الحين. التنبيهات الجديدة توصلك أول بأول.'
}

async function pumpAlerts () {
  if (!connected || pumping) return
  pumping = true
  try {
    let { alerts } = await (await door('GET', '/alerts')).json()
    // A long outage leaves a pile of old alerts: one summary, not 50 messages
    // (a burst like that is also how WhatsApp spots unofficial clients).
    let late = 0
    while (late < alerts.length && alertAge(alerts[late]) > LATE_ALERT_SECONDS) late++
    if (late >= SUMMARY_FROM) {
      // One summary in each language.
      const texts = settings().languages.map(lang => summaryOf(alerts.slice(0, late), lang))
      for (const jid of alertTargets()) await send(jid, texts)
      await door('POST', '/alerts/ack', { id: alerts[late - 1].id })
      log(`${late} late alerts (#${alerts[0].id}–#${alerts[late - 1].id}) sent as one summary`)
      alerts = alerts.slice(late)
    }
    for (const alert of alerts) {
      // Queued while WhatsApp was not linked: say so, a ring from hours ago is
      // not someone at the door now (in the language of each message).
      const old = alertAge(alert) > LATE_ALERT_SECONDS
      const texts = inLanguages(alert.texts, alert.message, 'ar')
        .map(([lang, text]) => old ? LATE_ALERT[lang] + text : text)
      // Fetched once, whatever the number of messages and chats.
      const image = texts.length ? await photoBytes(alert.photo) : null
      for (const jid of alertTargets()) await send(jid, texts, image)
      // Acknowledged only once every message went out (at least once).
      await door('POST', '/alerts/ack', { id: alert.id })
      log(`alert #${alert.id} ${alert.type} ${alert.event} sent`)
    }
  } catch (err) {
    // The door app is restarting, or WhatsApp refused: try again next round.
    if (!/ECONNREFUSED|fetch failed/.test(err.message)) log('alerts:', err.message)
  } finally {
    pumping = false
  }
}

async function showQr (qr) {
  try {
    await QRCode.toFile(QR_PNG, qr, { width: 420, margin: 2 })
    fs.chmodSync(QR_PNG, 0o600)
  } catch (err) { log('qr png:', err.message) }
  if (LINK || process.stdout.isTTY) console.log(await QRCode.toString(qr, { type: 'terminal', small: true }))
  log(`scan the QR: WhatsApp -> Settings -> Linked devices -> Link a device (also saved to ${path.relative(ROOT, QR_PNG)})`)
  writeStatus('qr')
  retry = 0   // WhatsApp answered: the internet is fine
}

// What came in while the door was offline (Pi off, no internet) WhatsApp hands
// over in batches, each one asked for with <ib><offline_batch/>.  Baileys asks
// for the first batch only, and WhatsApp holds everything after it -- new
// messages too -- until the next reconnect: after a night off, "افتح" reached
// the door only after many restarts.  So ask for the next batch each time the
// last one has come in, until WhatsApp says it holds nothing more
// (<ib><offline/>).  Returns a function that stops the watch.
function watchOfflineQueue (s, { quietMs = OFFLINE_QUIET_MS, tickMs = 1000 } = {}) {
  let holding = false   // WhatsApp announced held items and has not said "done"
  let lastSeen = 0      // when the last held item, or our last request, went by
  let items = 0
  let itemsAtAsk = 0
  let batches = 0
  let emptyAsks = 0

  s.ws.on('CB:ib,,offline_preview', node => {
    const preview = (Array.isArray(node.content) ? node.content : []).find(c => c.tag === 'offline_preview')
    const { count = '?', ...kinds } = preview?.attrs || {}
    holding = true
    lastSeen = Date.now()          // Baileys asks for the first batch itself
    items = itemsAtAsk = emptyAsks = 0
    batches = 1
    const parts = Object.entries(kinds).map(([kind, n]) => `${kind} ${n}`)
    log(`WhatsApp holds ${count} item(s) from while the door was offline` +
        (parts.length ? ` (${parts.join(', ')})` : ''))
  })
  s.ws.on('CB:ib,,offline', () => {
    if (holding && batches > 1) log(`caught up: ${items} held item(s) in ${batches} batches`)
    holding = false
  })
  for (const kind of ['message', 'receipt', 'notification', 'call']) {
    s.ws.on(`CB:${kind},offline`, () => { items++; lastSeen = Date.now() })
  }

  const timer = setInterval(() => {
    if (!holding || !s.ws.isOpen || Date.now() - lastSeen < quietMs) return
    if (items > itemsAtAsk) emptyAsks = 0
    else if (++emptyAsks >= OFFLINE_EMPTY_ASKS) {
      log(`WhatsApp sent nothing more after ${batches} batch(es), ${items} held item(s); not asking again`)
      holding = false
      return
    }
    lastSeen = Date.now()
    itemsAtAsk = items
    batches++
    if (batches % 10 === 0) log(`… ${items} held item(s) so far, asking for batch ${batches}`)
    s.sendNode({ tag: 'ib', attrs: {}, content: [{ tag: 'offline_batch', attrs: { count: OFFLINE_BATCH } }] })
      .catch(err => log('offline batch:', err.message))
  }, tickMs)
  return () => clearInterval(timer)
}

async function connect () {
  fs.mkdirSync(AUTH_DIR, { recursive: true, mode: 0o700 })
  fs.chmodSync(AUTH_DIR, 0o700)
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)
  const version = await waVersion()
  log(`WhatsApp Web version ${version ? version.join('.') : '(built-in)'}`)
  sock = makeWASocket({
    auth: state,
    version,
    browser: Browsers.ubuntu('Smart Door'),
    logger: pino({ level: process.env.WA_LOG_LEVEL || (DEBUG ? 'warn' : 'error') }),
    // false: your phone keeps its notifications while the door is linked.
    markOnlineOnConnect: false,
    // The door needs no chat history: none is downloaded or kept.
    syncFullHistory: false,
    shouldSyncHistoryMessage: () => false,
    shouldIgnoreJid: jid => isJidGroup(jid) || isJidBroadcast(jid) || isJidNewsletter(jid)
  })
  sock.ev.on('creds.update', saveCreds)
  const stopOfflineWatch = watchOfflineQueue(sock)

  if (PAIR && !linked(state.creds)) {
    setTimeout(async () => {
      try {
        const code = await sock.requestPairingCode(PAIR)
        console.log(`\nPairing code: ${code}\nWhatsApp -> Settings -> Linked devices -> Link a device -> Link with phone number instead\n`)
      } catch (err) { log('pairing code:', err.message) }
    }, 3000)
  }

  sock.ev.on('connection.update', async update => {
    const { connection, lastDisconnect, qr, isNewLogin } = update
    if (qr && !PAIR) await showQr(qr)
    if (isNewLogin) log('scanned on the phone: finishing the link ...')
    if (connection === 'open') {
      connected = true
      retry = 0
      try { fs.rmSync(QR_PNG, { force: true }) } catch {}
      const self = me()
      log(`connected as …${self.pn.slice(-4)}; listening to the "message yourself" chat` +
          (settings().owners.length ? ` and ${settings().owners.length} owner number(s)` : ''))
      writeStatus('connected', { number_tail: self.pn.slice(-4) })
      if (LINK) {
        log('linked. Start the service now: systemctl --user start door-whatsapp')
        setTimeout(() => process.exit(0), 3000)
      }
    }
    if (connection === 'close') {
      connected = false
      stopOfflineWatch()
      const code = lastDisconnect?.error?.output?.statusCode
      // Not linked yet and the QRs simply ran out (every ~2.7 min): routine.
      const qrExpired = !linked(sock?.authState?.creds) && code === DisconnectReason.timedOut
      if (!qrExpired) log(`connection closed: ${lastDisconnect?.error?.message || 'no reason given'} (code ${code ?? '-'})`)
      if (code === DisconnectReason.loggedOut) {
        log('logged out from the phone: the session is deleted; link again')
        fs.rmSync(AUTH_DIR, { recursive: true, force: true })
        writeStatus('logged_out')
      } else {
        writeStatus('reconnecting', { code })
      }
      if (code === DisconnectReason.connectionReplaced) {
        log('another copy of this bot took over the session; this one stops')
        process.exit(1)
      }
      // A QR ran out: show a new one right away.  Anything else (no internet,
      // WhatsApp refusing) waits longer each time, up to a minute.
      const wait = code === DisconnectReason.restartRequired ? 500
        : qrExpired ? 1000
          : Math.min(60000, 2000 * 2 ** retry++)
      setTimeout(() => connect().catch(err => { log('connect:', err.message); process.exit(1) }), wait)
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (DEBUG) {
      const self = me()
      for (const m of messages) {
        const jid = m.key?.remoteJid || ''
        const user = userOf(jid)
        const chat = user && (user === self.pn || user === self.lid) ? `self(${jid.split('@')[1]})` : (jid.split('@')[1] || '?')
        const kinds = Object.keys(normalizeMessageContent(m.message) || {}).join(',') || '-'
        let lidInfo = ''
        if (isLidUser(jid)) {
          let pn = ''
          try { pn = userOf(await sock.signalRepository.lidMapping.getPNForLID(jid)) } catch {}
          lidInfo = ` lidDigits=${user.length} myLidDigits=${self.lid.length} lidIsMyPn=${pn ? pn === self.pn : 'unknown'}` +
                    ` altIsMe=${userOf(m.key?.remoteJidAlt) === self.pn} chatNumber=…${pn ? pn.slice(-4) : '?'}`
        } else if (user && !chat.startsWith('self')) {
          lidInfo = ` chatNumber=…${user.slice(-4)}`
        }
        log(`debug upsert ${type}: fromMe=${Boolean(m.key?.fromMe)} chat=${chat}${lidInfo} kinds=${kinds}` +
            ` stub=${m.messageStubType ?? '-'}${m.messageStubParameters?.[0] ? ' "' + m.messageStubParameters[0] + '"' : ''}` +
            ` age=${Math.round(Date.now() / 1000 - Number(m.messageTimestamp || 0))}s ours=${sentIds.has(m.key?.id)}`)
      }
    }
    for (const msg of messages) {
      try { await onMessage(msg) } catch (err) { log('message:', err.message) }
    }
  })
}

async function main () {
  if (!DOOR_KEY || DOOR_KEY.length < 32) {
    log('DOOR_KEY missing in .env: the door API cannot be used')
    process.exit(1)
  }
  if (LOGOUT) {
    // Stop the service first (systemctl --user stop door-whatsapp): two copies
    // of one session knock each other off.
    const { state } = await useMultiFileAuthState(AUTH_DIR)
    let unlinked = !linked(state.creds)
    if (!unlinked) {
      sock = makeWASocket({ auth: state, version: await waVersion(), logger: pino({ level: 'error' }), markOnlineOnConnect: false })
      const opened = await new Promise(resolve => {
        setTimeout(() => resolve(false), 30000)
        sock.ev.on('connection.update', u => u.connection === 'open' && resolve(true))
      })
      if (opened) unlinked = await sock.logout().then(() => true, () => false)
    }
    fs.rmSync(AUTH_DIR, { recursive: true, force: true })
    writeStatus('logged_out')
    log(unlinked ? 'unlinked; session deleted'
      : 'session deleted here, but WhatsApp did not answer: remove the "Ubuntu" device on the phone (Settings -> Linked devices)')
    process.exit(0)
  }
  writeStatus('starting')
  await connect()
  setInterval(pumpAlerts, ALERT_EVERY_MS)
}

// For the bench test: the same functions, driven with a fake socket.
export const testing = {
  useSocket (fake) { sock = fake; connected = true },
  onMessage,
  pumpAlerts,
  sentIds,
  settings,
  languagesOf,
  inLanguages,
  summaryOf,
  watchOfflineQueue
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, () => {
      try { sock?.end(undefined) } catch {}
      writeStatus('stopped')
      process.exit(0)
    })
  }
  main().catch(err => { log('fatal:', err.stack || err.message); process.exit(1) })
}
