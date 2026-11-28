import { useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../../api/client";
import {
  personalLeaguesQueryOptions,
  personalQueryKeys,
  removeLeague,
} from "../../api/personalLeagues";
import {
  CONNECTION_ERROR_MESSAGE,
  throttledMessage,
} from "../../auth/accountMessages";
import { useAuth } from "../../auth/AuthProvider";
import { FormAlert } from "../../components/account/FormAlert";
import { FormField } from "../../components/account/FormField";
import type { PersonalLeague } from "../../types/personal";

function normalizedName(value: string): string {
  return value.trim().split(/\s+/).join(" ").toLowerCase();
}

function removalErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return throttledMessage(error, "League removal");
    }

    if (error.code === "league_removal_confirmation_mismatch") {
      return "Type the League name exactly as shown.";
    }

    if (error.status === 404) {
      return "This League is no longer on your account.";
    }

    return "The League could not be removed. Try again.";
  }

  return CONNECTION_ERROR_MESSAGE;
}

/** Remove one League from this account only; the League itself is never deleted. */
export function RemoveLeagueSection() {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const leaguesQuery = useQuery(personalLeaguesQueryOptions());
  const [selected, setSelected] = useState<PersonalLeague | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);

  const leagues = leaguesQuery.data?.leagues ?? [];
  const confirmed =
    selected !== null &&
    normalizedName(confirmation) === normalizedName(selected.league_name);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (submittingRef.current || selected === null || !confirmed) {
      return;
    }

    const target = selected;
    const removedSeasonIds = target.seasons.map((season) => season.season_id);

    submittingRef.current = true;
    setSubmitting(true);
    setError(null);

    try {
      await removeLeague(target.provider_league_id, confirmation, auth.csrfToken);

      // Only the removed League's Seasons leave the cache; other Leagues stay.
      await Promise.all(
        removedSeasonIds.map((seasonId) => {
          const key = personalQueryKeys.season(seasonId);

          return queryClient.cancelQueries({ queryKey: key }).then(() => {
            queryClient.removeQueries({ queryKey: key });
          });
        }),
      );
      const refreshed = await queryClient.fetchQuery({
        ...personalLeaguesQueryOptions(),
        staleTime: 0,
      });
      await auth.refreshUser();

      setSelected(null);
      setConfirmation("");
      setNotice(`${target.league_name} was removed from your account.`);

      if (refreshed.leagues.length === 0) {
        navigate("/account/import", { replace: true });
      }
    } catch (currentError) {
      if (currentError instanceof ApiError && currentError.status === 401) {
        auth.endSession();
        navigate("/login", { replace: true });
        return;
      }

      setError(removalErrorMessage(currentError));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="remove-league-heading">
      <h2 id="remove-league-heading">Remove a League from your account</h2>

      <p>
        Removing a League affects only this account: your access to it and the ESPN
        credentials you saved for it are deleted. League data other accounts use is not
        affected. You can import the League again later by proving your ESPN access
        again.
      </p>

      {notice ? <FormAlert tone="success">{notice}</FormAlert> : null}

      {leagues.length === 0 && !leaguesQuery.isPending ? (
        <p>You have no Leagues on this account.</p>
      ) : null}

      <ul className="remove-league-list">
        {leagues.map((league) => (
          <li key={`${league.provider}-${league.provider_league_id}`}>
            <span>{league.league_name}</span>
            <button
              type="button"
              className="auth-secondary-button"
              aria-label={`Remove ${league.league_name}`}
              disabled={submitting}
              onClick={() => {
                setSelected(league);
                setConfirmation("");
                setError(null);
                setNotice(null);
              }}
            >
              Remove
            </button>
          </li>
        ))}
      </ul>

      {selected ? (
        <form
          className="auth-form"
          aria-label={`Remove ${selected.league_name}`}
          noValidate
          onSubmit={(event) => {
            void handleSubmit(event);
          }}
        >
          <FormField
            id="remove-league-confirmation"
            label={`Type ${selected.league_name} to confirm`}
            name="confirmation"
            autoComplete="off"
            spellCheck={false}
            maxLength={200}
            value={confirmation}
            onChange={(event) => {
              setConfirmation(event.target.value);
            }}
          />

          {error ? <FormAlert tone="error">{error}</FormAlert> : null}

          <button
            className="auth-submit"
            type="submit"
            disabled={submitting || !confirmed}
            aria-busy={submitting}
          >
            {submitting ? "Removing League…" : "Remove League"}
          </button>

          <button
            type="button"
            className="auth-secondary-button"
            disabled={submitting}
            onClick={() => {
              setSelected(null);
              setConfirmation("");
              setError(null);
            }}
          >
            Keep this League
          </button>
        </form>
      ) : null}
    </section>
  );
}
