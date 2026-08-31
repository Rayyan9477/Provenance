"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * The screen index.
 *
 * The design presents fourteen screens grouped under RECORD / EVIDENCE / ACTIONS /
 * SYSTEM, and Judge mode and Counterfactual are first-class members of that index rather
 * than a hidden developer surface.
 *
 * `frontend/30_UX_SPEC.md` section 2.2 separately requires that exactly five destinations
 * be *primary*: Dashboard, Approvals, Add a document, Judge Mode (only when
 * `judge_mode_enabled`), and Sign out. Four of those five are here and carry
 * `data-primary="true"` so the requirement stays checkable from the DOM. Case detail,
 * State Proof, and individual approvals are absent from the index entirely, because each
 * needs an id the reader must first have chosen.
 *
 * Sign out is absent, and its absence is the honest reading of what this build does.
 *
 * The spec describes signing out as clearing a memory token, calling `POST /auth/logout`
 * to clear the cookie, and redirecting to the identity provider's logout endpoint. None
 * of those three exists here. This deployment authenticates server-side with a single
 * `PV_API_TOKEN` held in the server environment (see `lib/api/client.ts`), so there is no
 * per-reader session in the browser to end. The entry that used to sit at ordinal 14
 * linked to `/login`, which signed nobody out, ended nothing, and offered no way back to
 * the record -- a primary destination that was a dead end pretending to be a control.
 *
 * Restoring it needs the session this build does not have: a per-user cookie issued at
 * callback, a logout route that clears it, and a redirect to the provider's own logout.
 * Until those exist, no entry here can truthfully be called Sign out, and a control that
 * does nothing is worse than a control that is missing -- the missing one at least does
 * not tell the reader their session ended.
 */

interface Destination {
  readonly ordinal: string;
  readonly label: string;
  readonly href: string;
  readonly primary?: boolean;
  readonly flag?: "judge" | "counterfactual";
}

interface Group {
  readonly heading: string;
  readonly items: readonly Destination[];
}

const GROUPS: readonly Group[] = [
  {
    heading: "Record",
    items: [
      { ordinal: "01", label: "Dashboard", href: "/dashboard", primary: true },
      { ordinal: "02", label: "Case detail", href: "/cases" },
      { ordinal: "03", label: "Relationship", href: "/relationships" },
      { ordinal: "04", label: "State proof", href: "/proof" },
    ],
  },
  {
    heading: "Evidence",
    items: [
      { ordinal: "05", label: "Intake", href: "/ingest", primary: true },
      { ordinal: "06", label: "Artifact", href: "/artifacts" },
      { ordinal: "07", label: "Watches", href: "/watches" },
      { ordinal: "08", label: "Search", href: "/search" },
    ],
  },
  {
    heading: "Actions",
    items: [
      { ordinal: "09", label: "Approval", href: "/actions", primary: true },
      { ordinal: "10", label: "Audit export", href: "/export" },
    ],
  },
  {
    heading: "System",
    items: [
      { ordinal: "11", label: "Judge mode", href: "/judge", primary: true, flag: "judge" },
      {
        ordinal: "12",
        label: "Counterfactual",
        href: "/judge/counterfactual",
        flag: "counterfactual",
      },
      { ordinal: "13", label: "Settings", href: "/settings" },
    ],
  },
];

export interface NavigationProps {
  readonly judgeModeEnabled: boolean;
  readonly counterfactualEnabled: boolean;
}

function visible(item: Destination, props: NavigationProps): boolean {
  if (item.flag === "judge") return props.judgeModeEnabled;
  /* An absent flag is false. A surface that appears when a flag is missing is a leak. */
  if (item.flag === "counterfactual") return props.judgeModeEnabled && props.counterfactualEnabled;
  return true;
}

export function Navigation(props: NavigationProps) {
  const pathname = usePathname();

  return (
    <nav className="pv-nav" aria-label="Screens">
      {GROUPS.map((group) => (
        <div key={group.heading}>
          <p className="pv-label pv-nav-group-heading">{group.heading}</p>
          <ul>
            {group.items
              .filter((item) => visible(item, props))
              .map((item) => {
                const current = pathname === item.href || pathname.startsWith(`${item.href}/`);
                return (
                  <li key={item.ordinal}>
                    <Link
                      href={item.href}
                      className="pv-nav-item"
                      aria-current={current ? "page" : undefined}
                      data-primary={item.primary === true ? "true" : undefined}
                    >
                      <span className="pv-nav-ordinal" aria-hidden="true">
                        {item.ordinal}
                      </span>
                      <span>{item.label}</span>
                    </Link>
                  </li>
                );
              })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
