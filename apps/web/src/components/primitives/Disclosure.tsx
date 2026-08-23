"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

/**
 * The PROSE / TYPED RECORD disclosure control.
 *
 * This is not a theme. PROSE renders the record as sentences a person under mild
 * financial stress can act on; TYPED RECORD renders the same rows as raw field-level
 * state a database engineer can check. Neither view is a summary of the other -- they are
 * two renderings of the same rows, and the toggle chooses which.
 *
 * The control lives in one place so that every surface switches together. A page where
 * half the components honoured the toggle would let a reader believe the typed view was
 * complete when it was partial.
 */

export type DisclosureMode = "PROSE" | "TYPED";

interface DisclosureValue {
  readonly mode: DisclosureMode;
  readonly setMode: (mode: DisclosureMode) => void;
}

const DisclosureContext = createContext<DisclosureValue>({
  mode: "PROSE",
  setMode: () => undefined,
});

export function DisclosureProvider({
  children,
  initialMode = "PROSE",
}: {
  readonly children: ReactNode;
  readonly initialMode?: DisclosureMode;
}) {
  const [mode, setMode] = useState<DisclosureMode>(initialMode);
  const value = useMemo(() => ({ mode, setMode }), [mode]);
  return <DisclosureContext.Provider value={value}>{children}</DisclosureContext.Provider>;
}

export function useDisclosure(): DisclosureValue {
  return useContext(DisclosureContext);
}

export function DisclosureSwitch() {
  const { mode, setMode } = useDisclosure();
  const choose = useCallback((next: DisclosureMode) => () => setMode(next), [setMode]);

  return (
    <div>
      <span className="pv-label" id="pv-disclosure-label">
        Disclosure
      </span>
      <div
        className="pv-disclosure-switch"
        role="group"
        aria-labelledby="pv-disclosure-label"
        style={{ marginTop: "var(--pv-space-1)" }}
      >
        <button type="button" aria-pressed={mode === "PROSE"} onClick={choose("PROSE")}>
          Prose
        </button>
        <button type="button" aria-pressed={mode === "TYPED"} onClick={choose("TYPED")}>
          Typed record
        </button>
      </div>
    </div>
  );
}
