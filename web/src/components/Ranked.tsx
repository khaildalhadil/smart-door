import { useRef } from "react";
import { n, sum } from "../lib/format";
import { tr } from "../lib/i18n";
import type { Tallied } from "../lib/stats";
import { useTip } from "./Tooltip";

/** Ranked horizontal bars: one hue, largest first, the value at the tip. */
export function Ranked({ id, items, unit, empty }: { id: string; items: Tallied[]; unit: string; empty: string }) {
  if (!items.length) {
    return <div id={id} className="ranked"><p className="empty">{empty}</p></div>;
  }
  const max = items[0].value;
  const total = sum(items.map((item) => item.value));
  return (
    <div id={id} className="ranked">
      {items.map((item) => <Row key={item.name} item={item} max={max} total={total} unit={unit} />)}
    </div>
  );
}

function Row({ item, max, total, unit }: { item: Tallied; max: number; total: number; unit: string }) {
  const tip = useTip();
  const row = useRef<HTMLDivElement>(null);
  const share = Math.round((item.value / total) * 100);
  function show() {
    const box = row.current!.getBoundingClientRect();
    tip.show(item.name, [{ value: n(item.value), label: unit + " · " + share + tr("% من المجموع", "% of the total"),
                           color: "var(--series-1)" }], box.left + box.width / 2, box.top);
  }
  return (
    <div ref={row} className="ranked-row" tabIndex={0} role="group"
         aria-label={`${item.name}: ${n(item.value)} ${unit}${tr("، ", ", ")}${share}%`}
         onPointerEnter={show} onFocus={show} onPointerLeave={tip.hide} onBlur={tip.hide}>
      <span className="ranked-name">{item.name}</span>
      <div className="ranked-track">
        <div className="ranked-bar" style={{ width: `calc((100% - 86px) * ${(item.value / max).toFixed(4)})` }} />
        <span className="ranked-value">{n(item.value)}<small>{" · " + share + "%"}</small></span>
      </div>
    </div>
  );
}
