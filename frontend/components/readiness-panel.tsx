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
    checking: "Checking API readiness…",
    ready: "API ready",
    unavailable: "API unavailable. Start the local stack and try again.",
  }[state];

  return (
    <p className={`status status-${state}`} role="status" aria-live="polite">
      <span aria-hidden="true" className="status-dot" />
      {message}
    </p>
  );
}
