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
 * `judge_mode_enabled`), and Sign out. Both are honoured: the index shows fourteen, and
 * the five carry `data-primary="true"` so the spec's requirement stays checkable from the
 * DOM. Case detail, State Proof, and individual approvals are absent from the index
 * entirely, because each needs an id the reader must first have chosen.
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
      { ordinal: "14", label: "Sign out", href: "/login", primary: true },
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
