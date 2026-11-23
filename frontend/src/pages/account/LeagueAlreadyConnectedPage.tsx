import { Link } from "react-router-dom";
import { Users } from "lucide-react";

import { LEAGUE_ALREADY_CLAIMED_MESSAGE } from "../../auth/accountMessages";
import { AccountLayout } from "../../components/account/AccountLayout";
import { FormAlert } from "../../components/account/FormAlert";

/**
 * Public outcome of a losing duplicate league claim.
 *
 * The provisional registration and its session were removed by the
 * server. This page is static: it names no workspace, owner or account.
 */
export function LeagueAlreadyConnectedPage() {
  return (
    <AccountLayout
      headingId="league-connected-heading"
      label="League already connected"
      title="This league already has a workspace"
      icon={<Users aria-hidden="true" />}
    >
      <div className="auth-stack">
        <FormAlert tone="notice">{LEAGUE_ALREADY_CLAIMED_MESSAGE}</FormAlert>

        <p className="provider-setup-copy">
          Your provisional registration was removed, so no account or workspace was kept.
        </p>

        <nav className="auth-links" aria-label="Sign in options">
          <Link to="/login" replace>
            Administrator sign in
          </Link>
          <Link to="/login" replace state={{ mode: "spectator" }}>
            Spectator sign in
          </Link>
        </nav>
      </div>
    </AccountLayout>
  );
}
