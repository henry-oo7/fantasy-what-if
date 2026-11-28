import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet } from "react-router-dom";
import { AlertTriangle, RefreshCw } from "lucide-react";

import { ApiError } from "../../api/client";
import {
  personalLeaguesQueryOptions,
  personalQueryKeys,
  personalSeasonStatusQueryOptions,
  setDefaultSeason,
} from "../../api/personalLeagues";
import { useAuth } from "../../auth/AuthProvider";
import { useSeasonScope } from "../../personal/seasonScope";
import { useSeasonPolling } from "../../personal/useSeasonPolling";
import { describeSeason } from "../../personal/seasonState";
import type { PersonalSeasonDetail } from "../../types/personal";
import "./personal.css";

/** One bounded state for a missing, inaccessible or malformed Season. */
function SeasonNotFound() {
  return (
    <section className="app-state" aria-live="polite">
      <h1>Season not available</h1>
      <p>This Season does not exist or is not available to your account.</p>
      <Link to="/account">Back to your Leagues</Link>
    </section>
  );
}

function SeasonHeader({ season }: { season: PersonalSeasonDetail }) {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const leaguesQuery = useQuery(personalLeaguesQueryOptions());
  const state = describeSeason(season);
  const isDefault = leaguesQuery.data?.default_season_id === season.season_id;

  const makeDefault = useMutation({
    mutationFn: () => setDefaultSeason(season.season_id, auth.csrfToken),
    onSuccess: ({ default_season_id }) => {
      queryClient.setQueryData(personalQueryKeys.leagues(), (current) =>
        current ? { ...current, default_season_id } : current,
      );
    },
  });

  return (
    <header className="personal-season-header">
      <div>
        <p className="page-kicker">ESPN League</p>
        <h1>{season.league_name}</h1>
        <p className="personal-season-meta">
          <strong>{season.season_year} Season</strong>
          {isDefault ? <span className="personal-chip">Default Season</span> : null}
          {state.chip ? <span className="personal-chip">{state.chip}</span> : null}
        </p>
      </div>

      {leaguesQuery.data && !isDefault ? (
        <div>
          <button
            type="button"
            className="personal-secondary-button"
            disabled={makeDefault.isPending}
            aria-busy={makeDefault.isPending}
            onClick={() => {
              makeDefault.mutate();
            }}
          >
            {makeDefault.isPending ? "Saving…" : "Set as default Season"}
          </button>
          {makeDefault.isError ? (
            <p className="field-error" role="alert">
              The default Season could not be saved. Try again.
            </p>
          ) : null}
        </div>
      ) : null}
    </header>
  );
}

/**
 * Season-scoped frame for the personal analytics routes. The Season in the
 * URL is only a locator: GET /api/seasons/{id} authorizes it, and nothing
 * below renders until that Season's own status has loaded.
 */
export function PersonalSeasonLayout() {
  const { seasonId } = useSeasonScope();
  const queryClient = useQueryClient();
  const statusQuery = useQuery({
    ...personalSeasonStatusQueryOptions(seasonId ?? 0),
    enabled: seasonId !== undefined,
  });
  const season = statusQuery.data;

  const syncing = season?.sync_state === "queued" || season?.sync_state === "syncing";
  const polling = useSeasonPolling(seasonId, syncing);

  // When this Season's synchronization finishes, drop only its cached data so
  // the analytics refetch; the League list carries the sync summary. The
  // remembered state names its Season, so a Season switch never counts.
  const previousSync = useRef<{ seasonId: number; syncing: boolean } | null>(null);

  useEffect(() => {
    if (seasonId === undefined || season === undefined) {
      return;
    }

    const before = previousSync.current;
    previousSync.current = { seasonId, syncing };

    if (before?.seasonId === seasonId && before.syncing && !syncing) {
      void queryClient.invalidateQueries({ queryKey: personalQueryKeys.season(seasonId) });
      void queryClient.invalidateQueries({ queryKey: personalQueryKeys.leagues() });
    }
  }, [queryClient, season, seasonId, syncing]);

  if (seasonId === undefined) {
    return <SeasonNotFound />;
  }

  if (statusQuery.isPending) {
    return (
      <section className="app-state" aria-live="polite">
        <RefreshCw className="spinner" aria-hidden="true" />
        <h1>Loading Season {seasonId}</h1>
      </section>
    );
  }

  if (statusQuery.error instanceof ApiError && statusQuery.error.status === 404) {
    return <SeasonNotFound />;
  }

  if (statusQuery.isError && statusQuery.data === undefined) {
    return (
      <section className="app-state" aria-live="assertive">
        <h1>Unable to load this Season</h1>
        <p>Check your connection and try again.</p>
        <button
          type="button"
          onClick={() => {
            void statusQuery.refetch();
          }}
        >
          Try again
        </button>
      </section>
    );
  }

  const loaded = statusQuery.data as PersonalSeasonDetail;
  const state = describeSeason(loaded);

  return (
    <div className="personal-season">
      <SeasonHeader season={loaded} />

      {polling.exhausted ? (
        <div className="personal-notice personal-notice-warning" role="status">
          <AlertTriangle aria-hidden="true" />
          <span>This is taking longer than expected. The import may still finish.</span>
          <button
            type="button"
            className="personal-secondary-button"
            onClick={() => {
              polling.restart();
              void statusQuery.refetch();
            }}
          >
            Check again
          </button>
        </div>
      ) : null}

      {state.notice ? (
        <p
          className={`personal-notice personal-notice-${state.notice.tone}`}
          role={state.notice.tone === "warning" ? "alert" : "status"}
        >
          <AlertTriangle aria-hidden="true" />
          <span>{state.notice.text}</span>
          {loaded.access_status === "reverification_required" ? (
            <Link
              to="/account/import"
              state={{
                providerLeagueId: loaded.provider_league_id,
                seasonYear: loaded.season_year,
              }}
            >
              Reconnect this League
            </Link>
          ) : null}
        </p>
      ) : null}

      {state.unavailable ? (
        <section className="personal-unavailable" role="status" aria-live="polite">
          {state.unavailable.kind === "importing" ? (
            <RefreshCw className="spinner" aria-hidden="true" />
          ) : null}
          <h2>{state.unavailable.title}</h2>
          <p>{state.unavailable.text}</p>
          {state.unavailable.kind !== "importing" ? (
            <Link
              to="/account/import"
              state={{
                providerLeagueId: loaded.provider_league_id,
                seasonYear: loaded.season_year,
              }}
            >
              Import this Season again
            </Link>
          ) : null}
        </section>
      ) : (
        // Keyed by Season so no page state outlives a Season switch.
        <Outlet key={seasonId} />
      )}
    </div>
  );
}
