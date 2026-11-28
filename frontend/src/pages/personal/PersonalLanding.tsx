import { useQuery } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { RefreshCw } from "lucide-react";

import { personalLeaguesQueryOptions } from "../../api/personalLeagues";
import { landingSeasonId } from "../../personal/seasonState";

/** /account: open the default (or newest readable) Season, or send an empty account to import. */
export function PersonalLanding() {
  const leaguesQuery = useQuery(personalLeaguesQueryOptions());

  if (leaguesQuery.isPending) {
    return (
      <section className="app-state" aria-live="polite">
        <RefreshCw className="spinner" aria-hidden="true" />
        <h1>Loading your Leagues</h1>
      </section>
    );
  }

  if (leaguesQuery.isError) {
    return (
      <section className="app-state" aria-live="assertive">
        <h1>Unable to load your Leagues</h1>
        <p>Check your connection and try again.</p>
        <button
          type="button"
          onClick={() => {
            void leaguesQuery.refetch();
          }}
        >
          Try again
        </button>
      </section>
    );
  }

  const seasonId = landingSeasonId(leaguesQuery.data);

  return (
    <Navigate
      to={seasonId === null ? "/account/import" : `/account/seasons/${seasonId}`}
      replace
    />
  );
}
