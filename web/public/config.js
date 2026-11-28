// Dashboard settings, read when the page opens: change them without a rebuild.
// The two values come from the Supabase project ("Smart Door"):
//
//   Supabase dashboard -> Connect (or Settings -> API Keys)
//     url : the Project URL, e.g. "https://abcdefghijkl.supabase.co"
//     key : the PUBLISHABLE key ("sb_publishable_..."; the legacy "anon" key
//           also works)
//
// The publishable key is safe to put in a web page: row-level security lets a
// visitor read nothing until they sign in with an account that has a role in
// the door_members table (admin or member).  NEVER put the secret key ("sb_secret_..." or
// "service_role") here: that one belongs only in the .env file on the Pi.
//
// With both values empty the page offers a demo with made-up data; ?demo
// shows it in any case.

window.DOOR_CONFIG = {
  url: "https://hzwxmozkeunguqnfxsqj.supabase.co",
  key: "sb_publishable_Vcu3ExL5Kb0dpGtOexe-wg_foacEnR9",

  // Only change these if you changed [cloud] in config.ini on the Pi.
  table: "door_events",
  statusTable: "door_status",
  bucket: "door-photos",
  chatTable: "door_chat",   // supabase/chat.sql
};
