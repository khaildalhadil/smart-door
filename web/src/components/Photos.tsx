import { useEffect, useRef, useState } from "react";
import { GALLERY_STEP, SIGN_SECONDS } from "../lib/config";
import { fmtFull, when } from "../lib/format";
import { lang, tr } from "../lib/i18n";
import type { DoorEvent, Source } from "../lib/types";
import { CATEGORY_ICON, categoryName, describe, tabs } from "../lib/wording";
import { Icon } from "./Icons";

export function Tag({ r }: { r: DoorEvent }) {
  return (
    <span className={"tag " + r.category}>
      <Icon name={CATEGORY_ICON[r.category] || "gear"} />{categoryName(r.category)}
    </span>
  );
}

export function Tabs({ id, current, choose, extra = [] }: {
  id: string; current: string; choose: (tab: string) => void; extra?: [string, string][];
}) {
  return (
    <div id={id} className="segmented small" role="group">
      {[...tabs(), ...extra].map(([key, label]) => (
        <button key={key} type="button" aria-pressed={key === current} onClick={() => choose(key)}>{label}</button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------ signed links
/** Signed links for bucket paths: fetched in one request, reused until they expire. */
const signed = new Map<string, { url: string; until: number }>();
/** Paths the bucket had no picture for. */
const missing = new Set<string>();

function known(path: string): string {
  if (path.startsWith("data:")) return path;
  const hit = signed.get(path);
  return hit && hit.until > Date.now() ? hit.url : "";
}

async function resolve(source: Source, rows: DoorEvent[]): Promise<void> {
  const need = [...new Set(rows.map((r) => r.photo).filter((p) => p && !known(p) && !missing.has(p)))];
  if (!need.length) return;
  const map = await source.sign(need);
  for (const path of need) {
    const url = map[path];
    if (url) signed.set(path, { url, until: Date.now() + (SIGN_SECONDS - 300) * 1000 });
    else missing.add(path);
  }
}

function usePhotos(source: Source, rows: DoorEvent[], onError: (err: unknown) => void) {
  const [, setVersion] = useState(0);
  const key = rows.map((r) => r.uid).join(",");
  useEffect(() => {
    let alive = true;
    resolve(source, rows).then(() => alive && setVersion((v) => v + 1)).catch((err) => alive && onError(err));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, key]);
  return known;
}

/** The picture, or a clear "no picture" when the bucket has none or it won't load. */
function Picture({ path, url, alt, id, lazy }: { path: string; url: string; alt: string; id?: string; lazy?: boolean }) {
  const [broken, setBroken] = useState("");
  if (missing.has(path) || (url && broken === url)) {
    const text = tr("لا توجد صورة", "No picture");
    return (
      <span id={id} className="no-picture" role="img" aria-label={alt + " · " + text}>
        <Icon name="nophoto" /><span>{text}</span>
      </span>
    );
  }
  if (!url) return <img id={id} alt={alt} />;   // the link is on its way
  return <img id={id} src={url} alt={alt} loading={lazy ? "lazy" : undefined} decoding="async"
              onError={() => setBroken(url)} />;
}

// ------------------------------------------------------------------ gallery
export function Gallery({ source, rows, onOpen, onError }: {
  source: Source; rows: DoorEvent[]; onOpen: (list: DoorEvent[], at: number) => void;
  onError: (err: unknown) => void;
}) {
  const [tab, setTab] = useState("all");
  const [shown, setShown] = useState(GALLERY_STEP);
  const all = rows.filter((r) => r.photo && (tab === "all" || r.category === tab));
  const visible = all.slice(0, shown);
  const url = usePhotos(source, visible, onError);
  return (
    <section className="card" id="card-gallery">
      <div className="section-head">
        <div>
          <h2><Icon name="photo" />{tr("الصور", "Pictures")}</h2>
          <p className="muted">{tr("صورة من كاميرا الباب لحظة كل دخول أو محاولة أو رنّة جرس",
                                   "A picture from the door camera at each entry, attempt or doorbell ring")}</p>
        </div>
        <Tabs id="gallery-tabs" current={tab} choose={(t) => { setTab(t); setShown(GALLERY_STEP); }} />
      </div>
      <div id="gallery" className="gallery">
        {!visible.length && <p className="empty">{tr("لا توجد صور في هذه الفترة", "No pictures in this period")}</p>}
        {visible.map((r, i) => (
          <button key={r.uid} className="shot" type="button" onClick={() => onOpen(all, i)}>
            <Picture path={r.photo} url={url(r.photo)} alt={describe(r) + tr("، ", ", ") + when(r.t!)} lazy />
            <span className="shot-text">
              <span className="shot-what"><Tag r={r} />{r.person || ""}</span>
              <span className="shot-when">{describe(r) + " · " + when(r.t!)}</span>
            </span>
          </button>
        ))}
      </div>
      <div className="more">
        <button id="gallery-more" className="button" type="button" hidden={all.length <= visible.length}
                onClick={() => setShown(shown + GALLERY_STEP)}>{tr("عرض المزيد", "Show more")}</button>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------- viewer
export function Viewer({ source, list, at, setAt, onClose, onError }: {
  source: Source; list: DoorEvent[]; at: number; setAt: (i: number) => void; onClose: () => void;
  onError: (err: unknown) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const r = list[at];
  const url = usePhotos(source, r ? [r] : [], onError);

  useEffect(() => {
    const node = dialog.current;
    if (node && !node.open) node.showModal();
  }, []);

  if (!r) return null;
  const step = (by: number) => {
    const next = at + by;
    if (next >= 0 && next < list.length) setAt(next);
  };
  return (
    <dialog id="viewer" className="viewer" aria-label={tr("عرض الصورة", "Picture")} ref={dialog} onClose={onClose}
            onClick={(e) => { if (e.target === dialog.current) dialog.current?.close(); }}
            onKeyDown={(e) => {
              // Older pictures lie in the reading direction: left on the Arabic page, right on the English one.
              const older = lang() === "en" ? "ArrowRight" : "ArrowLeft";
              const newer = lang() === "en" ? "ArrowLeft" : "ArrowRight";
              if (e.key === older) step(1);
              if (e.key === newer) step(-1);
            }}>
      <div className="viewer-body">
        <Picture key={r.uid} id="viewer-img" path={r.photo} url={url(r.photo)}
                 alt={describe(r) + tr("، ", ", ") + when(r.t!)} />
        <div className="viewer-side">
          <div id="viewer-info">
            <Tag r={r} />
            <dl>
              <dt>{tr("الحدث", "Event")}</dt><dd>{describe(r)}</dd>
              {r.person && <><dt>{tr("الشخص", "Person")}</dt><dd>{r.person}</dd></>}
              <dt>{tr("الوقت", "Time")}</dt><dd>{fmtFull(r.t!)}</dd>
              <dt>{tr("السجل", "Log")}</dt><dd dir="ltr">{r.event + " " + (r.detail || "")}</dd>
            </dl>
          </div>
          <div className="viewer-nav">
            <button id="viewer-newer" className="button" type="button" disabled={at <= 0} onClick={() => step(-1)}>
              <Icon name="prev" />{tr("الأحدث", "Newer")}
            </button>
            <button id="viewer-older" className="button" type="button" disabled={at >= list.length - 1}
                    onClick={() => step(1)}>
              {tr("الأقدم", "Older")}<Icon name="next" />
            </button>
          </div>
        </div>
        <button id="viewer-close" className="button ghost viewer-close" type="button" aria-label={tr("إغلاق", "Close")}
                onClick={() => dialog.current?.close()}>
          <Icon name="close" />
        </button>
      </div>
    </dialog>
  );
}
