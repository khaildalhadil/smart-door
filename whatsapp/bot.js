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
const SENT_MEMORY = 500

const args = process.argv.slice(2)
const LINK = args.includes('--link')
const PAIR = args.includes('--pair') ? (args[args.indexOf('--pair') + 1] || '').replace(/\D/g, '') : ''
const LOGOUT = args.includes('--logout')

const log = (...parts) => console.log(new Date().toLocaleString('sv-SE'), ...parts)

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

// DOOR_API / DOOR_OWNERS in the environment override config.ini (bench tests).
function settings () {
  const ini = readIni(path.join(ROOT, 'config.ini'))
  const raw = process.env.DOOR_OWNERS ?? ini.bot?.owners ?? ''
  const owners = raw.split(/[,;]/).map(o => o.replace(/\D/g, '')).filter(o => o.length >= 6)
  const port = Number(ini.api?.port || 8000) || 8000
  return { owners, door: process.env.DOOR_API || `http://127.0.0.1:${port}` }
}

const DOOR_KEY = envValue('DOOR_KEY')

function writeStatus (state, extra = {}) {
  try {
    fs.mkdirSync(DATA, { recursive: true })
    fs.writeFileSync(STATUS_FILE, JSON.stringify({ state, time: new Date().toISOString(), ...extra }) + '\n', { mode: 0o600 })
  } catch (err) { log('status file:', err.message) }
}

// ------------------------------------------------------------------ door API
async function door (method, route, body) {
  const { door: base } = settings()
  const res = await fetch(base + route, {
    method,
    headers: { 'X-Door-Key': DOOR_KEY, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(15000)
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

function textOf (message) {
  const m = normalizeMessageContent(message)
  return (m?.conversation || m?.extendedTextMessage?.text || m?.buttonsResponseMessage?.selectedDisplayText ||
          m?.listResponseMessage?.title || m?.templateButtonReplyMessage?.selectedDisplayText || '').trim()
}

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

async function send (jid, text, photoRoute) {
  const image = await photoBytes(photoRoute)
  const sent = image
    ? await sock.sendMessage(jid, { image, caption: text })
    : await sock.sendMessage(jid, { text })
  remember(sent)
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
    if (age < TELL_STALE_SECONDS) {
      await send(asker.jid, '⏱ وصلتني رسالتك متأخرة (الباب كان غير متصل)، فما نفذتها. أرسلها مرة ثانية.')
    }
    return
  }
  let result
  try {
    result = await (await door('POST', '/command', { text, from: asker.from })).json()
  } catch (err) {
    log('command:', err.message)
    await send(asker.jid, '⚠️ برنامج الباب لا يرد الآن. جرّب بعد قليل.')
    return
  }
  log(`command from ${asker.selfChat ? 'self chat' : '…' + asker.from.slice(-4)}: ${result.intent}`)
  // Notes to yourself that are not door commands get no answer.
  if (!result.handled && asker.selfChat) return
  if (result.reply) await send(asker.jid, result.reply, result.photo)
}

function alertTargets () {
  const self = me()
  const owners = settings().owners
  const list = owners.length ? owners : [self.pn]
  return [...new Set(list)].filter(Boolean).map(n => `${n}@s.whatsapp.net`)
}

async function pumpAlerts () {
  if (!connected || pumping) return
  pumping = true
  try {
    const { alerts } = await (await door('GET', '/alerts')).json()
    for (const alert of alerts) {
      // Queued while WhatsApp was not linked: say so, a ring from hours ago is
      // not someone at the door now.
      const age = (Date.now() - Date.parse(alert.time || '')) / 1000
      const text = age > 300 ? '⏱ تنبيه متأخر (كان الواتساب غير متصل):\n' + alert.message : alert.message
      for (const jid of alertTargets()) await send(jid, text, alert.photo)
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
}

async function connect () {
  fs.mkdirSync(AUTH_DIR, { recursive: true, mode: 0o700 })
  fs.chmodSync(AUTH_DIR, 0o700)
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)
  let version
  try { ({ version } = await fetchLatestBaileysVersion()) } catch { /* use the built-in one */ }
  sock = makeWASocket({
    auth: state,
    version,
    browser: Browsers.ubuntu('Smart Door'),
    logger: pino({ level: 'error' }),
    // false: your phone keeps its notifications while the door is linked.
    markOnlineOnConnect: false,
    // The door needs no chat history: none is downloaded or kept.
    syncFullHistory: false,
    shouldSyncHistoryMessage: () => false,
    shouldIgnoreJid: jid => isJidGroup(jid) || isJidBroadcast(jid) || isJidNewsletter(jid)
  })
  sock.ev.on('creds.update', saveCreds)

  if (PAIR && !state.creds.registered) {
    setTimeout(async () => {
      try {
        const code = await sock.requestPairingCode(PAIR)
        console.log(`\nPairing code: ${code}\nWhatsApp -> Settings -> Linked devices -> Link a device -> Link with phone number instead\n`)
      } catch (err) { log('pairing code:', err.message) }
    }, 3000)
  }

  sock.ev.on('connection.update', async update => {
    const { connection, lastDisconnect, qr } = update
    if (qr && !PAIR) await showQr(qr)
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
      const code = lastDisconnect?.error?.output?.statusCode
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
      // Not linked yet: the QR simply ran out, so show a new one right away.
      const linking = !sock?.authState?.creds?.registered
      const wait = code === DisconnectReason.restartRequired ? 500
        : linking ? 1000
          : Math.min(60000, 2000 * 2 ** retry++)
      setTimeout(() => connect().catch(err => { log('connect:', err.message); process.exit(1) }), wait)
    }
  })

  sock.ev.on('messages.upsert', async ({ messages }) => {
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
    const { state } = await useMultiFileAuthState(AUTH_DIR)
    if (state.creds.registered) {
      sock = makeWASocket({ auth: state, logger: pino({ level: 'error' }), markOnlineOnConnect: false })
      await new Promise(resolve => sock.ev.on('connection.update', u => u.connection === 'open' && resolve()))
      await sock.logout().catch(() => {})
    }
    fs.rmSync(AUTH_DIR, { recursive: true, force: true })
    writeStatus('logged_out')
    log('unlinked; session deleted')
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
  sentIds
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
