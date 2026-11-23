import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { CalendarX2 } from "lucide-react";

import { ApiError } from "../../api/client";
import {
  cancelWorkspaceDeletion,
  workspaceDeletionStatusQueryOptions,
} from "../../api/workspaceDeletion";
import { useAuth } from "../../auth/AuthProvider";
import {
  WORKSPACE_NOT_RESTORABLE_MESSAGE,
  throttledMessage,
  unexpectedErrorMessage,
} from "../../auth/accountMessages";
import { AccountLayout } from "../../components/account/AccountLayout";
import { FormAlert } from "../../components/account/FormAlert";
import { PasswordField } from "../../components/account/PasswordField";
import {
  formatDeletionCountdown,
  formatDeletionDate,
} from "../../auth/workspaceDeletionFormat";

const COUNTDOWN_INTERVAL_MS = 30_000;

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, COUNTDOWN_INTERVAL_MS);

    return () => {
      window.clearInterval(timer);
    };
  }, []);

  return now;
}

/**
 * Shown right after a deletion request, when the session has ended.
 */
export function WorkspaceDeletionScheduledPage({
  scheduledFor,
}: {
  scheduledFor: string;
}) {
  return (
    <AccountLayout
      headingId="workspace-deletion-scheduled-heading"
      label="Workspace deletion"
      title="Deletion is scheduled"
      icon={<CalendarX2 aria-hidden="true" />}
      description={
        <p>
          This workspace will be permanently deleted on{" "}
          <strong>{formatDeletionDate(scheduledFor)}</strong>. Until then it is
          unavailable to you and to spectators. To cancel, sign in with your
          administrator email and password before that time.
        </p>
      }
    >
      <nav className="auth-links" aria-label="Account options">
        <Link to="/login">Sign in to cancel deletion</Link>
      </nav>
    </AccountLayout>
  );
}

/**
 * Restricted recovery screen for a deletion_recovery session.
 *
 * Shows only the schedule and the cancellation form. No provider, claim,
 * owner, job, credential, or internal identity detail is ever displayed.
 */
export function WorkspaceDeletionRecoveryPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const now = useNow();
  // Once this component ends the session, its status query must never
  // refetch into the freshly purged cache before the route unmounts it.
  const sessionEndedRef = useRef(false);
  const statusQuery = useQuery({
    ...workspaceDeletionStatusQueryOptions(),
    enabled: () => !sessionEndedRef.current,
  });

  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const submittingRef = useRef(false);

  const pending =
    statusQuery.data?.status === "deletion_pending" ? statusQuery.data : null;
  const statusUnavailable =
    statusQuery.error instanceof ApiError &&
    statusQuery.error.status === 409;
  const countdown =
    pending === null ? null : formatDeletionCountdown(pending.scheduled_for, now);
  const restorable =
    !unavailable &&
    !statusUnavailable &&
    pending !== null &&
    pending.cancellable &&
    countdown !== null;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (submittingRef.current || !restorable) {
      return;
    }

    const csrfToken = auth.csrfToken;
    const currentPassword = password;

    submittingRef.current = true;
    setSubmitting(true);
    setError(null);
    setPassword("");

    try {
      if (!csrfToken) {
        throw new ApiError("Missing session", 401, "Authentication is required.");
      }

      await cancelWorkspaceDeletion(
        { current_password: currentPassword },
        csrfToken,
      );
      sessionEndedRef.current = true;
      auth.endSession("workspace_deletion_canceled");
      navigate("/login", { replace: true });
    } catch (currentError) {
      if (currentError instanceof ApiError) {
        if (currentError.status === 409) {
          setUnavailable(true);
          return;
        }

        if (currentError.status === 401) {
          sessionEndedRef.current = true;
          auth.endSession();
          navigate("/login", { replace: true });
          return;
        }

        if (currentError.status === 429) {
          setError(throttledMessage(currentError, "cancellation"));
          return;
        }

        if (currentError.code === "invalid_current_password") {
          setError("The current password is incorrect.");
          return;
        }
      }

      setError(unexpectedErrorMessage(currentError));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  async function handleSignOut() {
    setSigningOut(true);

    try {
      await auth.logoutUser();
    } finally {
      setSigningOut(false);
      navigate("/login", { replace: true });
    }
  }

  let description;

  if (!restorable && (unavailable || statusUnavailable || pending !== null)) {
    description = <p>{WORKSPACE_NOT_RESTORABLE_MESSAGE}</p>;
  } else if (pending !== null) {
    description = (
      <>
        <p>
          This workspace is scheduled for permanent deletion on{" "}
          <strong>{formatDeletionDate(pending.scheduled_for)}</strong>.
        </p>
        <p className="deletion-countdown" aria-live="polite">
          {countdown}
        </p>
        <p>
          Cancel deletion to restore the workspace. You will need to sign in
          again afterwards.
        </p>
      </>
    );
  } else if (statusQuery.isError) {
    description = <p>{unexpectedErrorMessage(statusQuery.error)}</p>;
  } else {
    description = <p>Checking the deletion schedule…</p>;
  }

  return (
    <AccountLayout
      headingId="workspace-deletion-recovery-heading"
      label="Workspace deletion"
      title={restorable ? "Workspace scheduled for deletion" : "Workspace deletion"}
      icon={<CalendarX2 aria-hidden="true" />}
      description={description}
    >
      {restorable ? (
        <form
          className="auth-form"
          aria-label="Cancel workspace deletion"
          noValidate
          onSubmit={(event) => {
            void handleSubmit(event);
          }}
        >
          <PasswordField
            id="deletion-cancel-password"
            label="Current password"
            name="current_password"
            autoComplete="current-password"
            required
            maxLength={128}
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
            }}
          />

          {error ? <FormAlert tone="error">{error}</FormAlert> : null}

          <button
            className="auth-submit"
            type="submit"
            disabled={submitting || password.length === 0}
            aria-busy={submitting}
          >
            {submitting ? "Canceling deletion…" : "Cancel deletion"}
          </button>
        </form>
      ) : null}

      <button
        type="button"
        className="auth-secondary-button"
        disabled={signingOut}
        aria-busy={signingOut}
        onClick={() => {
          void handleSignOut();
        }}
      >
        {signingOut ? "Signing out…" : "Sign out"}
      </button>
    </AccountLayout>
  );
}
