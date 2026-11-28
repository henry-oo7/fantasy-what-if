import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import { Plus } from "lucide-react";

import { personalLeaguesQueryOptions } from "../../api/personalLeagues";
import "../../pages/personal/personal.css";
import { describeSeason } from "../../personal/seasonState";
import { useSeasonScope } from "../../personal/seasonScope";

/** Keep the current analytics section when moving to another Season. */
function seasonTarget(pathname: string, seasonId: number): string {
  const section = /^\/account\/seasons\/[^/]+\/([^/]+)/.exec(pathname)?.[1];

  return `/account/seasons/${seasonId}${section ? `/${section}` : ""}`;
}

/**
 * Connected Leagues and their readable Seasons, one flat list so every
 * choice is a real link: keyboard-operable, announced, and usable at 320px.
 */
export function LeagueSwitcher({ onNavigate }: { onNavigate?: () => void }) {
  const leaguesQuery = useQuery(personalLeaguesQueryOptions());
  const { seasonId: selectedSeasonId } = useSeasonScope();
  const location = useLocation();
  const data = leaguesQuery.data;

  return (
    <nav className="league-switcher" aria-label="Leagues and Seasons">
      {leaguesQuery.isPending ? (
        <p className="league-switcher-note" role="status">
          Loading your Leagues…
        </p>
      ) : null}

      {leaguesQuery.isError ? (
        <div className="league-switcher-note" role="alert">
          <p>Your Leagues could not be loaded.</p>
          <button
            type="button"
            onClick={() => {
              void leaguesQuery.refetch();
            }}
          >
            Try again
          </button>
        </div>
      ) : null}

      {data?.leagues.length === 0 ? (
        <p className="league-switcher-note">You have not imported a League yet.</p>
      ) : null}

      {data?.leagues.map((league) => {
        const headingId = `league-${league.provider}-${league.provider_league_id}`;

        return (
          <section key={headingId} aria-labelledby={headingId}>
            <h2 id={headingId} className="league-switcher-league">
              <span>{league.league_name}</span>
              <small>ESPN</small>
            </h2>

            <ul>
              {league.seasons.map((season) => {
                const selected = season.season_id === selectedSeasonId;
                const isDefault = season.season_id === data.default_season_id;
                const chip = describeSeason(season).chip;

                return (
                  <li key={season.season_id}>
                    <Link
                      to={seasonTarget(location.pathname, season.season_id)}
                      aria-current={selected ? "true" : undefined}
                      className={
                        selected
                          ? "league-switcher-season league-switcher-season-selected"
                          : "league-switcher-season"
                      }
                      onClick={onNavigate}
                    >
                      <strong>{season.season_year}</strong>
                      {isDefault ? <span>Default</span> : null}
                      {chip ? <span>{chip}</span> : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}

      <Link className="league-switcher-import" to="/account/import" onClick={onNavigate}>
        <Plus aria-hidden="true" />
        {data?.leagues.length === 0 ? "Import a League" : "Import another League"}
      </Link>
    </nav>
  );
}
