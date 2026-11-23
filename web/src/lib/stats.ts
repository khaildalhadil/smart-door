import { F_WEEKDAY, hourLabel, startOfDay } from "./format";
import type { DoorEvent } from "./types";
import { RANGE_DAYS, type Range } from "./wording";

export interface Bucket {
  label: string;    // under the column
  title: string;    // tooltip / table heading
  values: number[]; // one per series
}

export interface Tallied {
  name: string;
  value: number;
}

/** Counts per key, largest first. */
export function tally(rows: DoorEvent[], keyOf: (r: DoorEvent) => string | undefined): Tallied[] {
  const map = new Map<string, number>();
  for (const r of rows) {
    const key = keyOf(r);
    if (key) map.set(key, (map.get(key) || 0) + 1);
  }
  return Array.from(map, ([name, value]) => ({ name, value }))
    .sort((a, c) => c.value - a.value || (a.name < c.name ? -1 : 1));
}

export function rangeStart(range: Range): Date {
  const d = startOfDay(Date.now());
  d.setDate(d.getDate() - (RANGE_DAYS[range] - 1));
  return d;
}

/** Today: one column per hour.  Longer periods: one column per day. */
export function activityBuckets(range: Range, rows: DoorEvent[],
                                valuesOf: (r: DoorEvent, values: number[]) => void,
                                width: number): Bucket[] {
  const buckets: Bucket[] = [];
  if (range === "today") {
    for (let i = 0; i < 24; i++) {
      buckets.push({ label: String(i), title: "اليوم " + hourLabel(i) + " – " + hourLabel((i + 1) % 24),
                     values: new Array(width).fill(0) });
    }
    for (const r of rows) valuesOf(r, buckets[new Date(r.t!).getHours()].values);
    return buckets;
  }
  const start = rangeStart(range).getTime();
  const days = RANGE_DAYS[range];
  const index = new Map<number, number>();
  for (let i = 0; i < days; i++) {
    const d = new Date(start);
    d.setDate(d.getDate() + i);
    index.set(d.getTime(), i);
    buckets.push({ label: d.getDate() + "/" + (d.getMonth() + 1), title: F_WEEKDAY.format(d),
                   values: new Array(width).fill(0) });
  }
  for (const r of rows) {
    const at = index.get(startOfDay(r.t!).getTime());
    if (at !== undefined) valuesOf(r, buckets[at].values);
  }
  return buckets;
}

export function hourBuckets(rows: DoorEvent[]): Bucket[] {
  const buckets: Bucket[] = [];
  for (let h = 0; h < 24; h++) {
    buckets.push({ label: String(h), title: hourLabel(h) + " – " + hourLabel((h + 1) % 24), values: [0] });
  }
  for (const r of rows) buckets[new Date(r.t!).getHours()].values[0]++;
  return buckets;
}

/** The rows the headline numbers and charts are made of. */
export function split(rows: DoorEvent[]) {
  return {
    entries: rows.filter((r) => r.category === "entry"),
    denied: rows.filter((r) => r.category === "denied" && r.method !== "whatsapp"),
    rings: rows.filter((r) => r.event === "doorbell.ring"),
    alarms: rows.filter((r) => r.category === "alarm" && /DETECTED/.test(r.detail || "")),
  };
}
