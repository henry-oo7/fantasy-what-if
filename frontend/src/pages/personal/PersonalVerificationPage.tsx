import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { MailCheck } from "lucide-react";

import { useAuth } from "../../auth/AuthProvider";
import { AccountLayout } from "../../components/account/AccountLayout";
import { ResendVerification } from "../../components/account/ResendVerification";

/** Shown instead of every League route until the account's email is verified. */
export function PersonalVerificationPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);

  async function handleSignOut() {
    setSigningOut(true);

    try {
      await auth.logoutUser();
    } finally {
      setSigningOut(false);
      navigate("/login", { replace: true });
    }
  }

  return (
    <AccountLayout
      headingId="personal-verification-heading"
      label="Your account"
      title="Verify your email address"
      icon={<MailCheck aria-hidden="true" />}
      description={<p>Verify your email address using the link we sent you, then return here.</p>}
    >
      <ResendVerification email={auth.user?.email ?? undefined} />

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
