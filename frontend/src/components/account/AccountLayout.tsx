import type { ReactNode } from "react";
import { ShieldCheck } from "lucide-react";

import "../../pages/auth.css";

interface AccountLayoutProps {
  /** Visible card heading. */
  title: string;
  /** Short uppercase label above the heading. */
  label: string;
  /** Optional introductory copy below the heading. */
  description?: ReactNode;
  icon: ReactNode;
  children: ReactNode;
  /** Used to name the card region for assistive technology. */
  headingId: string;
}

/**
 * Shared public-account layout: league introduction plus one flat card.
 */
export function AccountLayout({
  title,
  label,
  description,
  icon,
  children,
  headingId,
}: AccountLayoutProps) {
  return (
    <main className="auth-page">
      <section className="auth-introduction">
        <div className="auth-brand">
          <ShieldCheck aria-hidden="true" />
          <span>Fantasy What-If</span>
        </div>

        <p className="eyebrow">Private league analytics</p>

        <p className="auth-headline">
          See what the standings do not show.
        </p>

        <p className="auth-description">
          Explore alternate schedules, all-play results,
          league-average matchups, and optimal weekly lineups
          for your league.
        </p>
      </section>

      <section
        className="auth-card"
        aria-labelledby={headingId}
      >
        <div className="auth-card-icon">{icon}</div>

        <p className="section-label">{label}</p>

        <h1 id={headingId}>{title}</h1>

        {description ? (
          <div className="auth-card-copy">{description}</div>
        ) : null}

        {children}
      </section>
    </main>
  );
}
