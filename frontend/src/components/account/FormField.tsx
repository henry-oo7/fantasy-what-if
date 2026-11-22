import {
  forwardRef,
  type InputHTMLAttributes,
} from "react";

interface FormFieldProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  id: string;
  label: string;
  hint?: string;
  error?: string | null;
}

/**
 * Labelled text input whose hint and error are announced with the field
 * through aria-describedby, with aria-invalid set while an error shows.
 */
export const FormField = forwardRef<HTMLInputElement, FormFieldProps>(
  function FormField({ id, label, hint, error, ...inputProps }, ref) {
    const hintId = hint ? `${id}-hint` : null;
    const errorId = error ? `${id}-error` : null;
    const describedBy =
      [hintId, errorId].filter(Boolean).join(" ") || undefined;

    return (
      <div className="form-field">
        <label htmlFor={id}>{label}</label>

        <input
          ref={ref}
          id={id}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          {...inputProps}
        />

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
