import { useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Trash2 } from "lucide-react";

import { ApiError } from "../../api/client";
import {
  requestWorkspaceDeletion,
  workspaceDeletionStatusQueryOptions,
} from "../../api/workspaceDeletion";
import { useAuth } from "../../auth/AuthProvider";
import {
  throttledMessage,
  unexpectedErrorMessage,
} from "../../auth/accountMessages";
import { normalizeConfirmationSlug } from "../../auth/workspaceDeletionFormat";

function requestErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return throttledMessage(error, "deletion");
    }

    if (error.code === "invalid_current_password") {
      return "The current password is incorrect.";
    }

    if (error.code === "workspace_slug_mismatch") {
      return "Type the workspace name exactly as shown to confirm.";
    }
  }

  return unexpectedErrorMessage(error);
}

/**
 * Danger zone: request FR-20 deletion of the active workspace.
 *
 * Requires the displayed workspace name and the current password. On
 * success every session is revoked by the server, so authentication
 * state and cached workspace data are cleared before routing to the
 * deletion-scheduled screen.
 */
export function WorkspaceDeletionSection() {
  const auth = useAuth();
  const navigate = useNavigate();
  // Once this component ends the session, its status query must never
  // refetch into the freshly purged cache before the route unmounts it.
  const sessionEndedRef = useRef(false);
  const statusQuery = useQuery({
    ...workspaceDeletionStatusQueryOptions(),
    enabled: () => !sessionEndedRef.current,
  });

  const [confirmation, setConfirmation] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);

  const review =
    statusQuery.data?.status === "active" ? statusQuery.data : null;
  const slugMatches =
    review !== null &&
    normalizeConfirmationSlug(confirmation) ===
      normalizeConfirmationSlug(review.confirmation_slug);
  const canSubmit = slugMatches && password.length > 0 && !submitting;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (submittingRef.current || review === null) {
      return;
    }

    if (!slugMatches) {
      setError("Type the workspace name exactly as shown to confirm.");
      return;
    }

    const csrfToken = auth.csrfToken;

    if (!csrfToken) {
      setError(
        "Your administrator session does not have a CSRF token. Sign in again.",
      );
      return;
    }

    if (
      !window.confirm(
        `Schedule "${review.confirmation_slug}" for permanent deletion in ${review.grace_period_days} days? Everyone will be signed out immediately.`,
      )
    ) {
      return;
    }

    const currentPassword = password;

    submittingRef.current = true;
    setSubmitting(true);
    setError(null);
    setPassword("");

    try {
      const accepted = await requestWorkspaceDeletion(
        {
          current_password: currentPassword,
          confirmation_slug: confirmation,
        },
        csrfToken,
      );

      sessionEndedRef.current = true;
      auth.endSession();
      navigate("/workspace-deletion", {
        replace: true,
        state: { deletionScheduledFor: accepted.scheduled_for },
      });
    } catch (currentError) {
      setError(requestErrorMessage(currentError));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <section
      className="admin-section admin-danger-zone"
      aria-labelledby="danger-zone-heading"
    >
      <div className="admin-section-heading">
        <div>
          <p className="admin-kicker">Workspace</p>

          <h2 id="danger-zone-heading">Danger zone</h2>
        </div>
      </div>

      <div className="admin-control-panel">
        <div className="admin-control-heading">
          <Trash2 aria-hidden="true" />

          <div>
            <h3>Delete this workspace</h3>

            <p>
              Deletion is scheduled{" "}
              {review !== null ? `${review.grace_period_days} days` : "30 days"}{" "}
              after you confirm. Everyone is signed out immediately and the
              workspace is unavailable during that grace period. You can cancel
              by signing in again before the scheduled time; after it, the
              workspace and its league data are permanently removed.
            </p>
          </div>
        </div>

        {review !== null ? (
          <form
            className="admin-deletion-form"
            aria-label="Request workspace deletion"
            noValidate
            onSubmit={(event) => {
              void handleSubmit(event);
            }}
          >
            <div className="admin-password-grid">
              <label htmlFor="deletion-confirmation-slug">
                Type <strong>{review.confirmation_slug}</strong> to confirm
                <input
                  id="deletion-confirmation-slug"
                  type="text"
                  value={confirmation}
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  maxLength={50}
                  disabled={submitting}
                  onChange={(event) => {
                    setConfirmation(event.target.value);
                  }}
                />
              </label>

              <label htmlFor="deletion-current-password">
                Current password
                <input
                  id="deletion-current-password"
                  type="password"
                  value={password}
                  autoComplete="current-password"
                  maxLength={128}
                  disabled={submitting}
                  onChange={(event) => {
                    setPassword(event.target.value);
                  }}
                />
              </label>
            </div>

            <div className="admin-actions">
              <button
                type="submit"
                className="admin-danger-button"
                disabled={!canSubmit}
                aria-busy={submitting}
              >
                {submitting ? "Requesting deletion..." : "Delete workspace"}
              </button>
            </div>
          </form>
        ) : statusQuery.isError ? (
          <p className="admin-error">
            Workspace deletion is unavailable right now.
          </p>
        ) : (
          <p>Loading deletion settings…</p>
        )}
      </div>

      {error ? (
        <p className="admin-error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
