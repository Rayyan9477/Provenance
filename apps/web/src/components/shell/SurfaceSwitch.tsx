"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * The DARK / LIGHT surface control.
 *
 * This is the one control in the application that is genuinely a preference rather than a
 * disclosure: it changes how the record looks and nothing about what it says. It is
 * therefore allowed to be local state, and it is deliberately not sent anywhere. There is
 * no endpoint for it in the API contract and inventing one would put a write in a build
 * that performs no writes.
 *
 * It reads the current value from the document rather than assuming a default, so the
 * pressed state describes the page rather than describing what this component last did.
 * Every colour token has both values (`tokens.css`), so nothing here changes meaning:
 * SUPPORTS and CONTRADICTS stay separable by glyph and rule style in both surfaces, which
 * is what `grayscale.test.tsx` exists to hold.
 */

type Surface = "light" | "dark";

function currentSurface(): Surface {
  if (typeof document === "undefined") return "light";
  return document.documentElement.dataset["theme"] === "dark" ? "dark" : "light";
}

export function SurfaceSwitch() {
  const [surface, setSurface] = useState<Surface>("light");

  useEffect(() => {
    setSurface(currentSurface());
  }, []);

  const choose = useCallback(
    (next: Surface) => () => {
      document.documentElement.dataset["theme"] = next;
      setSurface(next);
    },
    [],
  );

  return (
    <div className="pv-disclosure-switch" role="group" aria-label="Surface" data-surface={surface}>
      <button type="button" aria-pressed={surface === "dark"} onClick={choose("dark")}>
        Dark
      </button>
      <button type="button" aria-pressed={surface === "light"} onClick={choose("light")}>
        Light
      </button>
    </div>
  );
}
