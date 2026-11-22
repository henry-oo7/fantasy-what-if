import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { MailCheck, MailWarning, RefreshCw } from "lucide-react";

import { verifyEmail } from "../../api/auth";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import {
  throttledMessage,
  unexpectedErrorMessage,
} from "../../auth/accountMessages";
import { useOneTimeUrlToken } from "../../auth/useOneTimeUrlToken";
import { AccountLayout } from "../../components/account/AccountLayout";
import { FormAlert } from "../../components/account/FormAlert";
import { ResendVerification } from "../../components/account/ResendVerification";

type VerificationState =
  | { kind: "verifying" }
  | { kind: "missing" }
  | { kind: "invalid" }
  | { kind: "unavailable" }
  | { kind: "failed"; message: string };

export function VerifyEmailPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const { tokenRef, hadToken } = useOneTimeUrlToken();
  const submittedRef = useRef(false);
  const [state, setState] = useState<VerificationState>(() =>
    hadToken ? { kind: "verifying" } : { kind: "missing" },
  );

  useEffect(() => {
    // Submit exactly once, including under React StrictMode re-runs.
    if (submittedRef.current || tokenRef.current === null) {
      return;
    }

    submittedRef.current = true;
    const token = tokenRef.current;
    tokenRef.current = null;

    void (async () => {
      try {
        await verifyEmail(token);
        await auth.adoptVerifiedSession();
        navigate("/onboarding", { replace: true });
      } catch (error) {
        if (error instanceof ApiError && error.code === "invalid_email_verification") {
          setState({ kind: "invalid" });
        } else if (
          error instanceof ApiError &&
          error.code === "email_verification_unavailable"
        ) {
          setState({ kind: "unavailable" });
        } else if (error instanceof ApiError && error.status === 429) {
          setState({
            kind: "failed",
            message: throttledMessage(error, "verification"),
          });
        } else {
          setState({ kind: "failed", message: unexpectedErrorMessage(error) });
        }
      }
    })();
  }, [auth, navigate, tokenRef]);

  if (state.kind === "verifying") {
    return (
      <AccountLayout
        headingId="verify-heading"
        label="Verify your email"
        title="Verifying your email"
        icon={<RefreshCw className="spinner" aria-hidden="true" />}
        description={
          <p role="status">Confirming your verification link…</p>
        }
      >
        {null}
      </AccountLayout>
    );
  }

  if (state.kind === "unavailable") {
    return (
      <AccountLayout
        headingId="verify-heading"
        label="Verify your email"
        title="Verification is unavailable"
        icon={<MailWarning aria-hidden="true" />}
      >
        <FormAlert tone="error">
          This verification link can no longer be used for this workspace.
          If your email is already verified, sign in to continue.
        </FormAlert>

        <nav className="auth-links" aria-label="Account options">
          <Link to="/login">Sign in</Link>
        </nav>
      </AccountLayout>
    );
  }

  const message =
    state.kind === "missing"
      ? "This page needs the verification link from your email. Open the link again, or request a new one below."
      : state.kind === "invalid"
        ? "This verification link is invalid, expired, or already used. Request a new verification email below."
        : state.message;

  return (
    <AccountLayout
      headingId="verify-heading"
      label="Verify your email"
      title={
        state.kind === "failed"
          ? "Verification did not finish"
          : "Request a new verification link"
      }
      icon={
        state.kind === "failed" ? (
          <MailWarning aria-hidden="true" />
        ) : (
          <MailCheck aria-hidden="true" />
        )
      }
    >
      <div className="auth-stack">
        <FormAlert tone={state.kind === "missing" ? "notice" : "error"}>
          {message}
        </FormAlert>

        <ResendVerification />
      </div>

      <nav className="auth-links" aria-label="Account options">
        <Link to="/login">Back to sign in</Link>
      </nav>
    </AccountLayout>
  );
}
