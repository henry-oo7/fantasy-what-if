import { useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../../api/client";
import {
  importLeague,
  personalLeaguesQueryOptions,
  personalQueryKeys,
} from "../../api/personalLeagues";
import {
  CONNECTION_ERROR_MESSAGE,
  throttledMessage,
} from "../../auth/accountMessages";
import { useAuth } from "../../auth/AuthProvider";
import { validateProviderTarget } from "../../auth/providerTargetValidation";
import { FormAlert } from "../../components/account/FormAlert";
import { FormField } from "../../components/account/FormField";
import { PasswordField } from "../../components/account/PasswordField";
import type { LeagueImportRequest } from "../../types/personal";
import "./personal.css";

/** Backend codes whose detail is deliberately user-safe. Raw provider text is never shown. */
const SAFE_DETAIL_CODES = new Set([
  "provider_authentication_failed",
  "provider_validation_failed",
  "provider_unavailable",
  "provider_credentials_required",
  "unsupported_provider",
  "league_import_forbidden",
  "league_import_unavailable",
]);

interface FieldErrors {
  leagueId?: string;
  seasonYear?: string;
  swid?: string;
  espnS2?: string;
}

interface Prefill {
  providerLeagueId?: unknown;
  seasonYear?: unknown;
}

function importErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return throttledMessage(error, "League import");
    }

    if (error.code !== null && SAFE_DETAIL_CODES.has(error.code)) {
      return error.detail;
    }

    return "The League could not be imported. Try again.";
  }

  return CONNECTION_ERROR_MESSAGE;
}

/**
 * ESPN League import. Credentials, League ID and year live only in this
 * component's state: never in the URL, storage, a mutation cache or the
 * router state, and the credentials are cleared as soon as the request ends.
 */
export function ImportLeaguePage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const leaguesQuery = useQuery(personalLeaguesQueryOptions());
  // Non-secret identifiers only, set by the reconnect link.
  const prefill = location.state as Prefill | null;
  const [leagueId, setLeagueId] = useState(
    typeof prefill?.providerLeagueId === "string" ? prefill.providerLeagueId : "",
  );
  const [seasonYear, setSeasonYear] = useState(
    typeof prefill?.seasonYear === "number" ? String(prefill.seasonYear) : "",
  );
  const [isPrivate, setIsPrivate] = useState(false);
  const [swid, setSwid] = useState("");
  const [espnS2, setEspnS2] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [alert, setAlert] = useState<{ tone: "error" | "notice"; message: string } | null>(null);
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);

  // /api/auth/me is authoritative; an empty League list also reads as first import.
  const first =
    auth.user?.navigation_state === "import_required" ||
    leaguesQuery.data?.leagues.length === 0;

  function clearCredentials() {
    setSwid("");
    setEspnS2("");
    setFieldErrors((current) => ({ ...current, swid: undefined, espnS2: undefined }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (pendingRef.current) {
      return;
    }

    const errors: FieldErrors = validateProviderTarget(leagueId, seasonYear);

    if (isPrivate) {
      if (swid.trim().length === 0) {
        errors.swid = "Enter SWID. Both credentials are required for a private League.";
      }

      if (espnS2.trim().length === 0) {
        errors.espnS2 = "Enter espn_s2. Both credentials are required for a private League.";
      }
    }

    setFieldErrors(errors);

    if (Object.keys(errors).length > 0) {
      return;
    }

    const request: LeagueImportRequest = {
      provider: "espn",
      provider_league_id: leagueId.trim(),
      season_year: Number(seasonYear.trim()),
      ...(isPrivate
        ? { credentials: { swid: swid.trim(), espn_s2: espnS2.trim() } }
        : {}),
    };

    pendingRef.current = true;
    setPending(true);
    setAlert(null);

    try {
      const result = await importLeague(request, auth.csrfToken);

      clearCredentials();
      setIsPrivate(false);
      // The account may now be past import_required; the server decides.
      await auth.refreshUser().catch(() => undefined);
      await queryClient.invalidateQueries({ queryKey: personalQueryKeys.leagues() });
      await queryClient.invalidateQueries({
        queryKey: personalQueryKeys.season(result.season_id),
      });
      navigate(`/account/seasons/${result.season_id}`);
    } catch (error) {
      clearCredentials();

      if (error instanceof ApiError && error.code === "provider_credentials_required") {
        setIsPrivate(true);
      }

      setAlert({ tone: "error", message: importErrorMessage(error) });
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  return (
    <section className="personal-import" aria-labelledby="import-heading">
      <h1 id="import-heading">{first ? "Import your first League" : "Import another League"}</h1>

      <p>
        Enter an ESPN League and season year. Fantasy What-If only reads League data; it never
        changes your League on ESPN. Importing a League you can already read adds that Season
        to your account.
      </p>

      <form
        className="auth-form"
        noValidate
        aria-describedby={alert ? "import-alert" : undefined}
        onSubmit={(event) => {
          void handleSubmit(event);
        }}
      >
        <FormField
          id="import-league-id"
          label="ESPN League ID"
          hint="The number after leagueId= in your ESPN league address."
          inputMode="numeric"
          autoComplete="off"
          maxLength={15}
          value={leagueId}
          error={fieldErrors.leagueId}
          onChange={(event) => {
            setLeagueId(event.target.value);
          }}
        />

        <FormField
          id="import-season-year"
          label="Season year"
          hint="The ESPN season to import, for example the current season."
          inputMode="numeric"
          autoComplete="off"
          maxLength={4}
          value={seasonYear}
          error={fieldErrors.seasonYear}
          onChange={(event) => {
            setSeasonYear(event.target.value);
          }}
        />

        <label className="personal-checkbox">
          <input
            type="checkbox"
            checked={isPrivate}
            onChange={(event) => {
              setIsPrivate(event.target.checked);
              clearCredentials();
            }}
          />
          <span>This is a private League</span>
        </label>

        {isPrivate ? (
          <fieldset className="credential-fields">
            <legend>Private League access</legend>

            <p>
              SWID and espn_s2 are needed only for private ESPN Leagues. They are cookies ESPN
              stores in your browser after you sign in at espn.com. They are encrypted before
              they are saved and are never shown again.
            </p>

            <PasswordField
              id="import-swid"
              label="SWID"
              autoComplete="off"
              value={swid}
              error={fieldErrors.swid}
              onChange={(event) => {
                setSwid(event.target.value);
              }}
            />

            <PasswordField
              id="import-espn-s2"
              label="espn_s2"
              autoComplete="off"
              value={espnS2}
              error={fieldErrors.espnS2}
              onChange={(event) => {
                setEspnS2(event.target.value);
              }}
            />

            <button type="button" className="auth-link-button" onClick={clearCredentials}>
              Clear SWID and espn_s2
            </button>
          </fieldset>
        ) : null}

        {alert ? (
          <FormAlert tone={alert.tone} id="import-alert">
            {alert.message}
          </FormAlert>
        ) : null}

        <button type="submit" className="auth-submit" disabled={pending} aria-busy={pending}>
          {pending ? "Checking and importing…" : "Import League"}
        </button>
      </form>
    </section>
  );
}
