import {
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Link } from "react-router-dom";
import { MailCheck, UserPlus } from "lucide-react";

import { registerAccount } from "../../api/auth";
import { ApiError } from "../../api/client";
import {
  throttledMessage,
  unexpectedErrorMessage,
} from "../../auth/accountMessages";
import { AccountLayout } from "../../components/account/AccountLayout";
import { FormAlert } from "../../components/account/FormAlert";
import { FormField } from "../../components/account/FormField";
import { PasswordField } from "../../components/account/PasswordField";
import { ResendVerification } from "../../components/account/ResendVerification";

const MINIMUM_PASSWORD_LENGTH = 12;

type FieldName =
  | "displayName"
  | "email"
  | "password"
  | "confirmPassword";

type FieldErrors = Partial<Record<FieldName, string>>;

const FIELD_ORDER: FieldName[] = [
  "displayName",
  "email",
  "password",
  "confirmPassword",
];

const FIELD_IDS: Record<FieldName, string> = {
  displayName: "register-display-name",
  email: "register-email",
  password: "register-password",
  confirmPassword: "register-confirm-password",
};

interface PendingVerification {
  email: string;
}

/**
 * Usability checks only. The backend remains authoritative for slug
 * format, email validity, password policy, and uniqueness.
 */
function validate(values: Record<FieldName, string>): FieldErrors {
  const errors: FieldErrors = {};

  if (!values.displayName.trim()) {
    errors.displayName = "Enter your display name.";
  }

  if (!values.email.trim()) {
    errors.email = "Enter your email address.";
  }

  if (values.password.length < MINIMUM_PASSWORD_LENGTH) {
    errors.password = `Use at least ${MINIMUM_PASSWORD_LENGTH} characters.`;
  }

  if (!values.confirmPassword) {
    errors.confirmPassword = "Confirm your password.";
  } else if (values.confirmPassword !== values.password) {
    errors.confirmPassword = "Passwords do not match.";
  }

  return errors;
}

export function RegisterPage() {
  const [values, setValues] = useState<Record<FieldName, string>>({
    displayName: "",
    email: "",
    password: "",
    confirmPassword: "",
  });
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [pending, setPending] = useState<PendingVerification | null>(null);
  const submittingRef = useRef(false);
  const formRef = useRef<HTMLFormElement>(null);

  function update(field: FieldName, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
  }

  function focusField(field: FieldName) {
    formRef.current
      ?.querySelector<HTMLInputElement>(`#${FIELD_IDS[field]}`)
      ?.focus();
  }

  function clearPasswords() {
    setValues((current) => ({
      ...current,
      password: "",
      confirmPassword: "",
    }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (submittingRef.current) {
      return;
    }

    const errors = validate(values);
    setFieldErrors(errors);
    setFormError(null);

    const firstInvalid = FIELD_ORDER.find((field) => errors[field]);

    if (firstInvalid) {
      focusField(firstInvalid);
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);

    const email = values.email.trim();

    try {
      // A duplicate email returns the same response as a new
      // registration, so success is rendered identically.
      await registerAccount({
        display_name: values.displayName,
        email,
        password: values.password,
        confirm_password: values.confirmPassword,
      });

      clearPasswords();
      setPending({ email });
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.code === "registration_unavailable") {
          setFormError(
            "Registration is currently closed. Try again later.",
          );
          return;
        }

        if (error.status === 429) {
          setFormError(throttledMessage(error, "registration"));
          return;
        }

        if (error.status === 422 && error.code === null) {
          setFormError(
            "Some details are not valid. Check the email address and try again.",
          );
          return;
        }
      }

      setFormError(unexpectedErrorMessage(error));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  if (pending !== null) {
    return (
      <AccountLayout
        headingId="verification-pending-heading"
        label="Verify your email"
        title="Check your inbox"
        icon={<MailCheck aria-hidden="true" />}
        description={
          <>
            <p>
              If <span className="auth-email">{pending.email}</span> can be
              used for a new account, an email with a verification link will
              be sent. Check your email for the next step.
            </p>
            <p>The link expires after 24 hours.</p>
          </>
        }
      >
        <div className="auth-stack">
          <ResendVerification email={pending.email} />
        </div>

        <nav className="auth-links" aria-label="Account options">
          <Link to="/login">Back to sign in</Link>
        </nav>
      </AccountLayout>
    );
  }

  return (
    <AccountLayout
      headingId="register-heading"
      label="New account"
      title="Create your account"
      icon={<UserPlus aria-hidden="true" />}
      description={
        <p>
          Create your own account. You will verify your email before
          importing a fantasy league.
        </p>
      }
    >
      <form
        ref={formRef}
        className="auth-form"
        aria-label="Create an account"
        noValidate
        onSubmit={(event) => {
          void handleSubmit(event);
        }}
      >
        <FormField
          id={FIELD_IDS.displayName}
          label="Display name"
          name="display_name"
          type="text"
          autoComplete="name"
          required
          maxLength={100}
          value={values.displayName}
          error={fieldErrors.displayName}
          onChange={(event) => {
            update("displayName", event.target.value);
          }}
        />

        <FormField
          id={FIELD_IDS.email}
          label="Email"
          name="email"
          type="email"
          autoComplete="email"
          autoCapitalize="none"
          spellCheck={false}
          required
          maxLength={320}
          value={values.email}
          error={fieldErrors.email}
          onChange={(event) => {
            update("email", event.target.value);
          }}
        />

        <PasswordField
          id={FIELD_IDS.password}
          label="Password"
          name="new-password"
          autoComplete="new-password"
          required
          maxLength={128}
          hint={`At least ${MINIMUM_PASSWORD_LENGTH} characters.`}
          value={values.password}
          error={fieldErrors.password}
          onChange={(event) => {
            update("password", event.target.value);
          }}
        />

        <PasswordField
          id={FIELD_IDS.confirmPassword}
          label="Confirm password"
          name="confirm-password"
          autoComplete="new-password"
          required
          maxLength={128}
          value={values.confirmPassword}
          error={fieldErrors.confirmPassword}
          onChange={(event) => {
            update("confirmPassword", event.target.value);
          }}
        />

        {formError ? <FormAlert tone="error">{formError}</FormAlert> : null}

        <button
          className="auth-submit"
          type="submit"
          disabled={submitting}
          aria-busy={submitting}
        >
          {submitting ? "Creating account…" : "Create account"}
        </button>
      </form>

      <nav className="auth-links" aria-label="Account options">
        <Link to="/login">Already registered? Sign in</Link>
      </nav>
    </AccountLayout>
  );
}
