import { useCallback, useEffect, useRef, useState } from "react";
import { CHAT_MAX, CHAT_ROWS, CHAT_WAIT, ONLINE_WITHIN } from "../lib/config";
import { errorText } from "../lib/errors";
import { fmtTime } from "../lib/format";
import { lang, tr } from "../lib/i18n";
import type { ChatMessage, DoorStatus, Source } from "../lib/types";
import { Icon } from "./Icons";

/** Waiting for the door: written, or being answered. */
const BUSY = new Set(["pending", "working"]);

/** The door's reply in the page's language (both come from the Pi). */
function replyText(m: ChatMessage): string {
  const texts = m.texts || {};
  return (lang() === "en" ? texts.en || texts.ar : texts.ar || texts.en) || m.body;
}

/** One tap = one message, in the page's language: [button, text sent]. */
const quick = (): [string, string][] => [
  [tr("الحالة", "Status"), tr("الحالة", "status")],
  [tr("صورة", "Photo"), tr("صورة", "photo")],
  [tr("رمز ضيف", "Guest code"), tr("رمز ضيف", "guest code")],
  [tr("السجل", "Log"), tr("السجل", "log")],
];

/** Admins write to the door like on WhatsApp; the Pi answers (app/webchat.py). */
export function DoorChat({ source, status, now }: { source: Source; status: DoorStatus | null; now: number }) {
  const [messages, setMessages] = useState<ChatMessage[] | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<{ err: unknown } | null>(null);
  const [links, setLinks] = useState<Record<string, string>>({});
  const [clock, setClock] = useState(Date.now());
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const merge = useCallback((m: ChatMessage) => {
    setMessages((old) => {
      const base = old ?? [];
      const at = base.findIndex((x) => x.id === m.id);
      if (at >= 0) {
        const next = base.slice();
        next[at] = { ...base[at], ...m };
        return next;
      }
      return [...base, m].sort((a, b) => a.id - b.id);
    });
  }, []);

  const load = useCallback(async () => {
    try {
      setMessages(await source.chatList(CHAT_ROWS));
      setFailure(null);
    } catch (err) {
      setFailure({ err });
    }
  }, [source]);

  useEffect(() => {
    void load();
    return source.chatSubscribe(merge);
  }, [source, load, merge]);

  // While a message waits for its answer: a clock for the timeout, and a poll
  // in case this network blocks Realtime.
  const waiting = (messages ?? []).some((m) => m.sender === "admin" && BUSY.has(m.status));
  useEffect(() => {
    if (!waiting) return;
    const tick = window.setInterval(() => setClock(Date.now()), 1000);
    const poll = window.setInterval(() => void load(), 3000);
    return () => {
      clearInterval(tick);
      clearInterval(poll);
    };
  }, [waiting, load]);

  // Pictures in replies live in the private bucket: signed links, an hour each.
  useEffect(() => {
    const wanted = (messages ?? []).map((m) => m.photo)
      .filter((p) => p && !p.startsWith("data:") && !links[p]);
    if (!wanted.length) return;
    source.sign(wanted).then((map) => setLinks((old) => ({ ...old, ...map }))).catch(() => undefined);
  }, [messages, links, source]);

  // The newest message at the bottom, like a phone.
  useEffect(() => {
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const send = async (text: string) => {
    const body = text.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      merge(await source.chatSend(body.slice(0, CHAT_MAX)));
      setDraft("");
      setFailure(null);
      setClock(Date.now());
    } catch (err) {
      setFailure({ err });
    } finally {
      setSending(false);
      input.current?.focus();
    }
  };

  const clear = async () => {
    if (!window.confirm(tr("مسح كل رسائل هذه المحادثة؟", "Delete this whole conversation?"))) return;
    try {
      await source.chatClear();
      setMessages([]);
    } catch (err) {
      setFailure({ err });
    }
  };

  const seen = status ? Date.parse(status.updated_at) : 0;
  const online = Boolean(seen) && now - seen < ONLINE_WITHIN;
  const shown = messages ?? [];
  const answered = new Set(shown.filter((m) => m.sender === "door" && m.reply_to).map((m) => m.reply_to));
  const typing = shown.some((m) => m.sender === "admin" && m.status === "working" && !answered.has(m.id));

  return (
    <section className="card chat" id="card-chat" aria-labelledby="chat-title">
      <div className="chat-head">
        <h2 id="chat-title"><Icon name="chat" />{tr("مراسلة الباب", "Chat with the door")}</h2>
        <span id="chat-presence" className={"chat-presence" + (online ? " on" : "")}>
          <span className="dot" />{online ? tr("الباب متصل", "Door online") : tr("الباب غير متصل", "Door offline")}
        </span>
        {shown.length > 0 && (
          <button id="chat-clear" className="button ghost" type="button" onClick={() => void clear()}
                  title={tr("مسح المحادثة", "Clear the chat")}>
            <Icon name="trash" /><span className="hide-narrow">{tr("مسح", "Clear")}</span>
          </button>
        )}
      </div>
      <p className="muted">
        {tr("نفس أوامر واتساب: افتح، اقفل، الحالة، صورة، رمز ضيف، السجل. والرسائل الحرة يفهمها OpenAI.",
            "The same commands as on WhatsApp: open, lock, status, photo, guest code, log. Free text is understood by OpenAI.")}
      </p>
      {status && !online && (
        <p className="chat-note">
          {tr("الباب غير متصل الآن. الرسالة تُنفَّذ فقط إذا وصلته خلال 90 ثانية من إرسالها.",
              "The door is offline right now. A message is carried out only if it reaches the door within 90 seconds.")}
        </p>
      )}

      <div id="chat-list" className="chat-list" ref={list} role="log" aria-live="polite"
           aria-label={tr("رسائل المحادثة", "Chat messages")}>
        {messages === null ? (
          <p className="empty">{failure ? "" : tr("جارٍ التحميل…", "Loading…")}</p>
        ) : shown.length === 0 ? (
          <p className="empty">{tr("ابدأ المحادثة: اكتب «الحالة» أو اختر زرًّا من الأسفل.",
                                   "Start the chat: type “status” or pick a button below.")}</p>
        ) : shown.map((m) => {
          const t = Date.parse(m.created_at);
          if (m.sender === "door") {
            const url = m.photo.startsWith("data:") ? m.photo : links[m.photo];
            return (
              <div key={m.id} className="bubble door" data-intent={m.intent}>
                <span className="who"><Icon name="door" />{tr("الباب", "Door")}</span>
                <p dir="auto">{replyText(m)}</p>
                {m.photo && (url ? (
                  <a href={url} target="_blank" rel="noreferrer">
                    <img src={url} alt={tr("صورة من كاميرا الباب", "Picture from the door camera")} />
                  </a>
                ) : <span className="muted">{tr("جارٍ تحميل الصورة…", "Loading the picture…")}</span>)}
                <span className="meta">{fmtTime(t)}</span>
              </div>
            );
          }
          const late = BUSY.has(m.status) && !answered.has(m.id) && clock - t > CHAT_WAIT;
          return (
            <div key={m.id} className="chat-row mine">
              <div className="bubble mine" data-status={m.status}>
                <p dir="auto">{m.body}</p>
                <span className="meta">
                  {fmtTime(t)}
                  {m.status === "done" ? <Icon name="check" />
                    : m.status === "stale" || m.status === "error" ? <Icon name="alert" />
                    : <Icon name="clock" />}
                </span>
              </div>
              {late && (
                <p className="chat-late" role="status">
                  {tr("الباب ما رد خلال 45 ثانية، ممكن يكون غير متصل بالإنترنت.",
                      "No answer from the door in 45 seconds; it may be offline.")}
                  <button type="button" onClick={() => void send(m.body)} disabled={sending}>
                    {tr("أعد الإرسال", "Send again")}
                  </button>
                </p>
              )}
            </div>
          );
        })}
        {typing && (
          <div className="bubble door typing" aria-label={tr("الباب يكتب…", "The door is typing…")}>
            <span /><span /><span />
          </div>
        )}
      </div>

      {failure && <p id="chat-error" className="form-error" role="alert">{errorText(failure.err)}</p>}

      <div className="chat-quick" role="group" aria-label={tr("رسائل جاهزة", "Quick messages")}>
        {quick().map(([label, text]) => (
          <button key={label} type="button" onClick={() => void send(text)} disabled={sending}>{label}</button>
        ))}
      </div>
      <form className="chat-compose" onSubmit={(e) => { e.preventDefault(); void send(draft); }}>
        <textarea id="chat-input" ref={input} rows={1} value={draft} maxLength={CHAT_MAX} dir="auto"
                  placeholder={tr("اكتب رسالة للباب…", "Write to the door…")}
                  aria-label={tr("رسالة للباب", "Message to the door")}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      void send(draft);
                    }
                  }} />
        <button id="chat-send" className="button primary" type="submit" disabled={sending || !draft.trim()}
                aria-label={tr("إرسال", "Send")} title={tr("إرسال", "Send")}>
          <Icon name="send" />
        </button>
      </form>
    </section>
  );
}