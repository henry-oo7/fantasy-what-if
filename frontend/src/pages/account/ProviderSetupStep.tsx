import { useState, type FormEvent } from "react";

import { validateProviderAccess } from "../../api/onboarding";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import {
  CONNECTION_ERROR_MESSAGE,
  throttledMessage,
} from "../../auth/accountMessages";
import { FormAlert } from "../../components/account/FormAlert";
import { FormField } from "../../components/account/FormField";
import { PasswordField } from "../../components/account/PasswordField";
import type { ProviderValidationRequest } from "../../types/onboarding";

const MINIMUM_SEASON_YEAR = 2000;
const LEAGUE_ID_PATTERN = /^[0-9]{1,15}$/;

/** Backend codes whose detail is safe and actionable to show as is. */
const SAFE_DETAIL_CODES = new Set([
  "provider_authentication_failed",
  "provider_validation_failed",
  "provider_unavailable",
  "unsupported_provider",
  "onboarding_stage_invalid",
]);

const PLANNED_PROVIDERS = ["Sleeper", "Yahoo"] as const;

interface FieldErrors {
  leagueId?: string;
  seasonYear?: string;
  swid?: string;
  espnS2?: string;
}

interface Alert {
  tone: "error" | "notice";
  message: string;
}

function validateTarget(leagueId: string, seasonYear: string): FieldErrors {
  const errors: FieldErrors = {};
  const currentYear = new Date().getFullYear();
  const trimmedId = leagueId.trim();

  if (!LEAGUE_ID_PATTERN.test(trimmedId) || /^0+$/.test(trimmedId)) {
    errors.leagueId = "Enter the numeric League ID from your ESPN league address.";
  }

  const year = Number(seasonYear.trim());

  if (
    !/^[0-9]{4}$/.test(seasonYear.trim()) ||
    year < MINIMUM_SEASON_YEAR ||
    year > currentYear
  ) {
    errors.seasonYear = `Enter a season year from ${MINIMUM_SEASON_YEAR} to ${currentYear}.`;
  }

  return errors;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return throttledMessage(error, "league access");
    }

    if (error.code !== null && SAFE_DETAIL_CODES.has(error.code)) {
      return error.detail;
    }

    return "League access could not be checked. Try again.";
  }

  return CONNECTION_ERROR_MESSAGE;
}

/**
 * Provider selection, ESPN league identification and access validation.
 *
 * Public leagues need only the League ID and season. Credential fields
 * appear only after the server reports that ESPN requires them. All four
 * values live only in this component's state and are cleared on success.
 */
export function ProviderSetupStep({ priorFailure }: { priorFailure: boolean }) {
  const auth = useAuth();
  const [provider, setProvider] = useState<"espn" | null>(null);
  const [leagueId, setLeagueId] = useState("");
  const [seasonYear, setSeasonYear] = useState("");
  const [needsCredentials, setNeedsCredentials] = useState(false);
  const [swid, setSwid] = useState("");
  const [espnS2, setEspnS2] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [alert, setAlert] = useState<Alert | null>(null);
  const [pending, setPending] = useState(false);

  function clearCredentials() {
    setSwid("");
    setEspnS2("");
    setFieldErrors((current) => ({ ...current, swid: undefined, espnS2: undefined }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (pending) {
      return;
    }

    const errors = validateTarget(leagueId, seasonYear);

    if (needsCredentials) {
      const hasSwid = swid.trim().length > 0;
      const hasEspnS2 = espnS2.trim().length > 0;

      if (!hasSwid) {
        errors.swid = "Enter SWID. Both credentials are required for a private league.";
      }

      if (!hasEspnS2) {
        errors.espnS2 = "Enter espn_s2. Both credentials are required for a private league.";
      }
    }

    setFieldErrors(errors);

    if (Object.keys(errors).length > 0) {
      return;
    }

    const request: ProviderValidationRequest = {
      provider: "espn",
      league_id: leagueId.trim(),
      season_year: Number(seasonYear.trim()),
      ...(needsCredentials
        ? { credentials: { swid: swid.trim(), espn_s2: espnS2.trim() } }
        : {}),
    };

    setPending(true);
    setAlert(null);

    try {
      const result = await validateProviderAccess(request, auth.csrfToken);

      if (result.status === "credentials_required") {
        setNeedsCredentials(true);
        setAlert({
          tone: "notice",
          message:
            "This ESPN league is private. Enter SWID and espn_s2 so Fantasy What-If can read it.",
        });
        return;
      }

      setLeagueId("");
      setSeasonYear("");
      setSwid("");
      setEspnS2("");
      setNeedsCredentials(false);
      // /api/auth/me now reports league_claim_pending; the page follows it.
      await auth.refreshUser();
    } catch (error) {
      setAlert({ tone: "error", message: errorMessage(error) });
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="provider-setup" aria-labelledby="provider-setup-heading">
      <h2 id="provider-setup-heading">Connect your fantasy league</h2>

      <p className="provider-setup-copy">
        Connect your fantasy league to explore deeper statistics, alternate outcomes,
        and what-if scenarios. Fantasy What-If only reads league data; it never changes
        your league on ESPN.
      </p>

      {priorFailure ? (
        <FormAlert tone="notice">
          The last league access check did not succeed. Check the details and try again.
        </FormAlert>
      ) : null}

      <fieldset className="provider-options">
        <legend>Fantasy provider</legend>

        <label className="provider-option">
          <input
            type="radio"
            name="provider"
            value="espn"
            checked={provider === "espn"}
            onChange={() => {
              setProvider("espn");
            }}
          />
          <span className="provider-option-name">ESPN</span>
          <span className="provider-option-status">Available</span>
        </label>

        {PLANNED_PROVIDERS.map((name) => (
          <label key={name} className="provider-option provider-option-planned">
            <input type="radio" name="provider" value={name.toLowerCase()} disabled />
            <span className="provider-option-name">{name}</span>
            <span className="provider-option-status">Coming later</span>
          </label>
        ))}
      </fieldset>

      {provider === "espn" ? (
        <form
          className="auth-form"
          noValidate
          aria-describedby={alert ? "provider-setup-alert" : undefined}
          onSubmit={(event) => {
            void handleSubmit(event);
          }}
        >
          <p className="provider-setup-copy">
            Your League ID and season year identify your existing ESPN league. This step
            checks whether Fantasy What-If can read it. It does not claim or import the
            league yet.
          </p>

          <FormField
            id="espn-league-id"
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
            id="espn-season-year"
            label="Season year"
            hint="The ESPN season to check first, for example the current season."
            inputMode="numeric"
            autoComplete="off"
            maxLength={4}
            value={seasonYear}
            error={fieldErrors.seasonYear}
            onChange={(event) => {
              setSeasonYear(event.target.value);
            }}
          />

          {needsCredentials ? (
            <fieldset className="credential-fields">
              <legend>Private league access</legend>

              <p className="provider-setup-copy">
                SWID and espn_s2 are required only for private ESPN leagues. They are
                cookies ESPN stores in your browser after you sign in at espn.com; you
                can find them in your browser&apos;s developer tools under stored
                cookies for espn.com. They are encrypted before they are saved and are
                never shown again.
              </p>

              <PasswordField
                id="espn-swid"
                label="SWID"
                autoComplete="off"
                value={swid}
                error={fieldErrors.swid}
                onChange={(event) => {
                  setSwid(event.target.value);
                }}
              />

              <PasswordField
                id="espn-s2"
                label="espn_s2"
                autoComplete="off"
                value={espnS2}
                error={fieldErrors.espnS2}
                onChange={(event) => {
                  setEspnS2(event.target.value);
                }}
              />

              <button
                type="button"
                className="auth-link-button"
                onClick={clearCredentials}
              >
                Clear SWID and espn_s2
              </button>
            </fieldset>
          ) : null}

          {alert ? (
            <FormAlert tone={alert.tone} id="provider-setup-alert">
              {alert.message}
            </FormAlert>
          ) : null}

          <button
            type="submit"
            className="auth-submit"
            disabled={pending}
            aria-busy={pending}
          >
            {pending ? "Checking league access…" : "Check league access"}
          </button>
        </form>
      ) : null}
    </section>
  );
}
