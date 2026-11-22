import type { ReactNode } from "react";

interface FormAlertProps {
  tone: "error" | "notice" | "success";
  children: ReactNode;
  id?: string;
}

/**
 * Form-level message. Errors interrupt (role="alert"); notices and
 * successes are announced politely (role="status").
 */
export function FormAlert({ tone, children, id }: FormAlertProps) {
  const className =
    tone === "error"
      ? "form-error"
      : tone === "success"
        ? "form-success"
        : "form-notice";

  return (
    <div
      id={id}
      className={className}
      role={tone === "error" ? "alert" : "status"}
    >
      {children}
    </div>
  );
}
