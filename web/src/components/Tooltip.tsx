import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useRef, useState,
         type ReactNode } from "react";

export interface TipLine {
  value: string;
  label: string;
  color?: string;
}

interface Tip {
  title: string;
  lines: TipLine[];
  x: number;   // viewport pixels
  y: number;
}

interface TipApi {
  show: (title: string, lines: TipLine[], x: number, y: number) => void;
  hide: () => void;
}

const TipContext = createContext<TipApi>({ show: () => {}, hide: () => {} });
export const useTip = () => useContext(TipContext);

/** One tooltip for the whole page, placed above the point it explains. */
export function TooltipProvider({ children }: { children: ReactNode }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const show = useCallback((title: string, lines: TipLine[], x: number, y: number) =>
    setTip({ title, lines, x, y }), []);
  const hide = useCallback(() => setTip(null), []);
  const api = useMemo(() => ({ show, hide }), [show, hide]);

  useLayoutEffect(() => {
    const node = box.current;
    if (!node || !tip) return;
    const rect = node.getBoundingClientRect();
    const left = Math.min(Math.max(8, tip.x - rect.width / 2), window.innerWidth - rect.width - 8);
    let top = tip.y - rect.height - 12;
    if (top < 8) top = tip.y + 18;
    node.style.left = left + "px";
    node.style.top = top + "px";
  }, [tip]);

  useLayoutEffect(() => {
    const onScroll = () => setTip(null);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <TipContext.Provider value={api}>
      {children}
      <div id="tooltip" className="tooltip" role="tooltip" hidden={!tip} ref={box}>
        {tip && (
          <>
            {tip.title && <div className="tooltip-title">{tip.title}</div>}
            {tip.lines.map((line, i) => (
              <div className="tooltip-row" key={i}>
                {line.color && <i className="tooltip-key" style={{ background: line.color }} />}
                <b>{line.value}</b>
                <span>{line.label}</span>
              </div>
            ))}
          </>
        )}
      </div>
    </TipContext.Provider>
  );
}
