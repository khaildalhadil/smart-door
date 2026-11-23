import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { n, sum } from "../lib/format";
import type { Bucket } from "../lib/stats";
import { Icon } from "./Icons";
import { useTip, type TipLine } from "./Tooltip";

export interface Series {
  name: string;
  color: string;
}

export interface ChartProps {
  id: string;
  buckets: Bucket[];
  series: Series[];
  unit?: string;            // noun after the number in the tooltip
  anchor?: "start" | "end"; // "end": the last column always gets its axis label
  summary: string;          // sentence for screen readers
  empty?: string;
}

function niceStep(max: number): number {
  const steps = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000];
  for (const step of steps) if (max / step <= 4) return step;
  return Math.ceil(max / 4);
}

function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  r = Math.max(0, Math.min(r, w / 2, h));
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

const TOP = 18, PLOT = 180, BOTTOM = 24, LEFT = 30, RIGHT = 6;

/** Stacked columns on one baseline; arrow keys walk the columns. */
export function ColumnChart({ id, buckets, series, unit, anchor = "start", summary, empty }: ChartProps) {
  const host = useRef<HTMLDivElement>(null);
  const bands = useRef<(SVGRectElement | null)[]>([]);
  const [width, setWidth] = useState(0);
  const [active, setActive] = useState(-1);
  const tip = useTip();

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    setWidth(node.clientWidth);
    const observer = new ResizeObserver(() => setWidth(node.clientWidth));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const totals = buckets.map((b) => sum(b.values));
  const max = Math.max(0, ...totals);

  function activate(i: number) {
    setActive(i);
    const hit = bands.current[i];
    const bucket = buckets[i];
    if (!hit || !bucket) return;
    const lines: TipLine[] = series.length > 1
      ? [...series.map((one, s) => ({ value: n(bucket.values[s]), label: one.name, color: one.color })),
         { value: n(totals[i]), label: "المجموع" }]
      : [{ value: n(totals[i]), label: unit || series[0].name, color: series[0].color }];
    const box = hit.getBoundingClientRect();
    const step = niceStep(max), ceiling = Math.ceil(max / step) * step;
    const y = TOP + PLOT - (totals[i] / ceiling) * PLOT;
    tip.show(bucket.title, lines, box.left + box.width / 2, box.top + (y - TOP) * (box.height / PLOT));
  }
  function deactivate() {
    setActive(-1);
    tip.hide();
  }
  function onKey(event: KeyboardEvent) {
    if (!buckets.length) return;
    const last = buckets.length - 1;
    let at = active < 0 ? last : active;
    if (event.key === "ArrowRight") at = Math.min(last, at + 1);
    else if (event.key === "ArrowLeft") at = Math.max(0, at - 1);
    else if (event.key === "Home") at = 0;
    else if (event.key === "End") at = last;
    else if (event.key === "Escape") return deactivate();
    else return;
    event.preventDefault();
    activate(at);
  }

  let body = null;
  if (!max) {
    body = <p className="empty">{empty || "لا توجد بيانات في هذه الفترة"}</p>;
  } else if (width) {
    const w = Math.max(220, width);
    const step = niceStep(max), ceiling = Math.ceil(max / step) * step;
    const band = (w - LEFT - RIGHT) / buckets.length;
    const bar = Math.max(1.5, Math.min(24, band > 7 ? band - 4 : band - 1));
    const y = (value: number) => TOP + PLOT - (value / ceiling) * PLOT;
    const every = Math.max(1, Math.ceil(buckets.length / Math.max(1, Math.floor((w - LEFT - RIGHT) / 46))));
    const peak = totals.lastIndexOf(max);
    const grid = [];
    for (let v = step; v <= ceiling; v += step) grid.push(v);
    body = (
      <svg viewBox={`0 0 ${w} ${TOP + PLOT + BOTTOM}`} height={TOP + PLOT + BOTTOM} aria-hidden="true"
           onPointerLeave={deactivate}>
        {grid.map((v) => (
          <g key={v}>
            <line className="gridline" x1={LEFT} x2={w - RIGHT} y1={y(v)} y2={y(v)} />
            <text x={LEFT - 6} y={y(v) + 4} textAnchor="end">{n(v)}</text>
          </g>
        ))}
        <text x={LEFT - 6} y={y(0) + 4} textAnchor="end">0</text>
        {buckets.map((bucket, i) => {
          const x = LEFT + i * band + (band - bar) / 2;
          let below = 0;
          let lastWithValue = -1;
          bucket.values.forEach((value, s) => { if (value > 0) lastWithValue = s; });
          const labelled = anchor === "end" ? (buckets.length - 1 - i) % every === 0 : i % every === 0;
          return (
            <g key={i}>
              {bucket.values.map((value, s) => {
                if (value <= 0) return null;
                const y0 = y(below), y1 = y(below + value);
                below += value;
                const isTop = s === lastWithValue;
                // A 2px gap in the surface colour separates the parts of a stack.
                const gap = isTop ? 0 : Math.min(2, Math.max(0, y0 - y1 - 1));
                const height = Math.max(1, y0 - y1 - gap);
                const style = { fill: series[s].color };
                return isTop
                  ? <path key={s} d={roundedTop(x, y1, bar, height, 4)} style={style} />
                  : <rect key={s} x={x} y={y1 + gap} width={bar} height={height} style={style} />;
              })}
              {labelled && (
                <text x={LEFT + i * band + band / 2} y={TOP + PLOT + 16} textAnchor="middle">{bucket.label}</text>
              )}
              {i === peak && (
                <text className="peak" x={LEFT + i * band + band / 2} y={y(max) - 5} textAnchor="middle">{n(max)}</text>
              )}
            </g>
          );
        })}
        <line className="baseline" x1={LEFT} x2={w - RIGHT} y1={y(0)} y2={y(0)} />
        {buckets.map((_, i) => (
          // The whole column slot is the hover target, not just the painted bar.
          <rect key={"hit" + i} ref={(el) => { bands.current[i] = el; }}
                className={"band" + (i === active ? " active" : "")}
                x={LEFT + i * band} y={TOP} width={band} height={PLOT}
                onPointerEnter={() => activate(i)} onPointerDown={() => activate(i)} />
        ))}
      </svg>
    );
  }

  return (
    <div className="chart" id={id} ref={host} tabIndex={0} role="img" aria-label={summary}
         onKeyDown={onKey}
         onFocus={() => buckets.length && max && activate(active >= 0 ? active : buckets.length - 1)}
         onBlur={deactivate}>
      {body}
    </div>
  );
}

/** The table twin of a column chart: every value, readable without hovering. */
export function ChartTable({ id, first, buckets, series }: {
  id: string; first: string; buckets: Bucket[]; series: Series[];
}) {
  const multi = series.length > 1;
  return (
    <details className="table-view">
      <summary><Icon name="table" />عرض كجدول</summary>
      <div id={id}>
        <div>
          <table>
            <thead>
              <tr>
                <th>{first}</th>
                {series.map((one) => <th key={one.name}>{one.name}</th>)}
                {multi && <th>المجموع</th>}
              </tr>
            </thead>
            <tbody>
              {[...buckets].reverse().map((bucket, i) => (
                <tr key={i}>
                  <td>{bucket.title}</td>
                  {bucket.values.map((value, s) => <td key={s}>{n(value)}</td>)}
                  {multi && <td>{n(sum(bucket.values))}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </details>
  );
}
