import { useCallback, useEffect, useRef, useState } from "react";
import { CloudSource } from "./lib/cloud";
import { configured } from "./lib/config";
import { DemoSource } from "./lib/demo";
import { errorText, sessionLost } from "./lib/errors";
import type { Me, Role, Source } from "./lib/types";
import { Dashboard } from "./components/Dashboard";
import { Gate, type GateMode } from "./components/Gate";
import { IconSprite } from "./components/Icons";
import { TooltipProvider } from "./components/Tooltip";

type View =
  | { kind: "boot" }
  | { kind: "gate"; mode: GateMode; message?: string; email?: string }
  | { kind: "app"; source: Source; me: Me };

export default function App() {
  const [view, setView] = useState<View>({ kind: "boot" });
  const cloud = useRef<CloudSource | null>(null);
  const viewRef = useRef(view);
  viewRef.current = view;

  const showGate = useCallback((mode: GateMode, message?: string, email?: string) => {
    setView({ kind: "gate", mode, message, email });
  }, []);

  /** Signed in (or demo): ask the database who this is before showing anything. */
  const enter = useCallback(async (source: Source) => {
    let me: Me | null;
    try {
      me = await source.me();
    } catch (err) {
      if (sessionLost(err)) showGate("login", errorText(err));
      else showGate("denied", errorText(err));
      return;
    }
    if (!me) showGate("login");
    else if (!me.role) showGate("denied", "", me.email);
    else setView({ kind: "app", source, me });
  }, [showGate]);

  const demo = useCallback((role: Role) => void enter(new DemoSource(role)), [enter]);

  const signOut = useCallback(async () => {
    const current = viewRef.current;
    showGate("login");
    try {
      if (current.kind === "app") await current.source.signOut();
      else await cloud.current?.signOut();
    } catch {
      /* signed out locally anyway */
    }
  }, [showGate]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("demo")) {
      demo(params.get("demo") === "member" ? "member" : "admin");
      return;
    }
    if (!configured) {
      showGate("setup");
      return;
    }
    (async () => {
      try {
        cloud.current = new CloudSource(() => {
          // Signed out in another tab, or the account was deleted.
          const current = viewRef.current;
          if (current.kind === "app" && !current.source.demo) showGate("login");
        });
        if (await cloud.current.hasSession()) await enter(cloud.current);
        else showGate("login");
      } catch (err) {
        showGate("login", errorText(err));
      }
    })();
  }, [demo, enter, showGate]);

  async function login(email: string, password: string) {
    try {
      await cloud.current!.signIn(email, password);
      await enter(cloud.current!);
    } catch (err) {
      showGate("login", errorText(err));
    }
  }

  return (
    <TooltipProvider>
      <IconSprite />
      {view.kind === "gate" && (
        <Gate mode={view.mode} message={view.message} email={view.email} onLogin={login}
              onLogout={() => void signOut()} onDemo={demo} />
      )}
      {view.kind === "app" && (
        <Dashboard key={view.source.demo ? "demo" : view.me.user_id} source={view.source} me={view.me}
                   onGate={showGate} onSignOut={() => void signOut()} />
      )}
    </TooltipProvider>
  );
}
