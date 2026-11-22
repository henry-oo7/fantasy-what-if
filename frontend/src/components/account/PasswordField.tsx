import {
  forwardRef,
  useState,
  type InputHTMLAttributes,
} from "react";
import { Eye, EyeOff } from "lucide-react";

interface PasswordFieldProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "type"> {
  id: string;
  label: string;
  hint?: string;
  error?: string | null;
}

/**
 * Password input with an accessible show/hide control.
 *
 * The toggle is a real button with an accessible name and aria-pressed
 * state. The value lives only in the owning form's component state.
 */
export const PasswordField = forwardRef<HTMLInputElement, PasswordFieldProps>(
  function PasswordField({ id, label, hint, error, ...inputProps }, ref) {
    const [visible, setVisible] = useState(false);
    const hintId = hint ? `${id}-hint` : null;
    const errorId = error ? `${id}-error` : null;
    const describedBy =
      [hintId, errorId].filter(Boolean).join(" ") || undefined;

    return (
      <div className="form-field">
        <label htmlFor={id}>{label}</label>

        <div className="password-input">
          <input
            ref={ref}
            id={id}
            type={visible ? "text" : "password"}
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy}
            autoCapitalize="none"
            spellCheck={false}
            {...inputProps}
          />

          <button
            type="button"
            className="password-toggle"
            aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
            aria-pressed={visible}
            aria-controls={id}
            onClick={() => {
              setVisible((current) => !current);
            }}
          >
            {visible ? (
              <EyeOff aria-hidden="true" />
            ) : (
              <Eye aria-hidden="true" />
            )}
          </button>
        </div>

        {hint ? <small id={hintId ?? undefined}>{hint}</small> : null}

        {error ? (
          <p className="field-error" id={errorId ?? undefined}>
            {error}
          </p>
        ) : null}
      </div>
    );
  },
);
