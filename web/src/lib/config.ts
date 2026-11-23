/** Settings from public/config.js, read at run time: change them without a rebuild. */
export interface DoorConfig {
  url: string;
  key: string;
  table: string;
  statusTable: string;
  bucket: string;
}

declare global {
  interface Window {
    DOOR_CONFIG?: Partial<DoorConfig>;
  }
}

export const CFG: DoorConfig = {
  url: "",
  key: "",
  table: "door_events",
  statusTable: "door_status",
  bucket: "door-photos",
  ...(typeof window !== "undefined" ? window.DOOR_CONFIG : {}),
};

export const configured = Boolean(CFG.url && CFG.key);

export const PAGE = 1000;            // rows per request
export const MAX_PAGES = 20;         // at most 20,000 rows per period
export const GALLERY_STEP = 24;
export const LOG_STEP = 25;
export const ONLINE_WITHIN = 3 * 60 * 1000;   // a heartbeat older than this = offline
export const SIGN_SECONDS = 3600;              // signed photo links last an hour
export const AUDIT_ROWS = 20;
