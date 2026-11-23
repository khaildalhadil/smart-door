/* Smart Lock dashboard (React + Supabase).
 *
 * Reads the door's history from Supabase (tables door_events / door_status,
 * bucket door-photos; see supabase/schema.sql) and shows it: headline numbers,
 * charts, a short written analysis, the camera pictures and the event log.
 * Nothing here can open the door or change the door's data.
 *
 * Two roles, decided by the database (door_members + row-level security): an
 * admin sees everything and manages the dashboard accounts; a member sees the
 * door's status, numbers and history, without the camera pictures, the
 * WhatsApp / panel / system records and the accounts.  The page only leaves
 * out what the database would return empty or refuse anyway.
 *
 * ?demo (or ?demo=member) runs on made-up data without Supabase.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
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
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
