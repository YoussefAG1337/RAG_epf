"use client";

import { useEffect, useState } from "react";
import { fetchReadiness } from "../lib/api";

type ReadinessState = "checking" | "ready" | "unavailable";

// While the API is unreachable (for example still starting), check again this often.
export const READINESS_RETRY_MS = 5000;

export function ReadinessPanel() {
  const [state, setState] = useState<ReadinessState>("checking");

  useEffect(() => {
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const check = () => {
      fetchReadiness()
        .then(() => active && setState("ready"))
        .catch(() => {
          if (!active) return;
          setState("unavailable");
          retry = setTimeout(check, READINESS_RETRY_MS);
        });
    };
    check();
    return () => {
      active = false;
      clearTimeout(retry);
    };
  }, []);

  const message = {
    checking: "Connexion au serveur…",
    ready: "Serveur connecté",
    unavailable: "Serveur injoignable, nouvelle tentative…",
  }[state];
  const dot = { checking: "bg-slate-400", ready: "bg-emerald-500", unavailable: "bg-red-500" }[state];

  return (
    <p className="flex items-center gap-2 text-xs text-slate-500" role="status" aria-live="polite">
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${dot}`} />
      {message}
    </p>
  );
}
