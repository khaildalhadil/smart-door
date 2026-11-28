/* Smart Lock dashboard (React + Supabase).
 *
 * Reads the door's history from Supabase (tables door_events / door_status,
 * bucket door-photos; see supabase/schema.sql) and shows it: headline numbers,
 * charts, a short written analysis, the camera pictures and the event log.
 * The one way it acts on the door: an admin's chat (components/Chat.tsx),
 * which the Pi answers like a WhatsApp message (app/webchat.py).
 *
 * Two roles, decided by the database (door_members + row-level security): an
 * admin sees everything and manages the dashboard accounts; a member sees the
 * door's status, numbers and history, without the camera pictures, the
 * WhatsApp / panel / system records and the accounts.  The page only leaves
 * out what the database would return empty or refuse anyway.
 *
 * ?demo (or ?demo=member) runs on made-up data without Supabase.
 * Arabic or English (?lang=en): the switch at the top, kept in this browser.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { applyLang } from "./lib/i18n";
import "./styles.css";

function applyTheme() {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem("door-theme");
  } catch {
    /* private window */
  }
  const theme = new URLSearchParams(window.location.search).get("theme") || stored;
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
}

applyTheme();
applyLang();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
