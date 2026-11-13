// Dashboard settings.  Fill in the two values from your Supabase project:
//
//   Supabase dashboard -> Connect (or Settings -> API Keys)
//     url : the Project URL, e.g. "https://abcdefghijkl.supabase.co"
//     key : the PUBLISHABLE key ("sb_publishable_..."; the legacy "anon" key
//           also works)
//
// The publishable key is safe to put in a web page: row-level security lets a
// visitor read nothing until they sign in with an account listed in the
// door_viewers table.  NEVER put the secret key ("sb_secret_..." or
// "service_role") here: that one belongs only in the .env file on the Pi.
//
// With both values empty the page offers a demo with made-up data.

window.DOOR_CONFIG = {
  url: "",
  key: "",

  // Only change these if you changed [cloud] in config.ini on the Pi.
  table: "door_events",
  statusTable: "door_status",
  bucket: "door-photos",
};
