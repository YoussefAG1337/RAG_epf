"use client";

import { useEffect, useState } from "react";
import { fetchReadiness } from "../lib/api";

type ReadinessState = "checking" | "ready" | "unavailable";

export function ReadinessPanel() {
  const [state, setState] = useState<ReadinessState>("checking");

  useEffect(() => {
    let active = true;
    fetchReadiness()
      .then(() => active && setState("ready"))
      .catch(() => active && setState("unavailable"));
    return () => {
      active = false;
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
