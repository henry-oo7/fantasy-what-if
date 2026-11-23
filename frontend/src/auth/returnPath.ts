/**
 * Public account routes are never valid post-login destinations: returning
 * to them would loop or replay a one-time token flow.
 */
const ACCOUNT_PATH_PREFIXES = [
  "/login",
  "/register",
  "/verify-email",
  "/forgot-password",
  "/reset-password",
  "/onboarding",
  "/league-already-connected",
  "/setup",
];

/**
 * Return a same-origin, in-application path or null.
 *
 * Rejects absolute URLs, protocol-relative URLs (`//host`), backslash
 * tricks (`/\host`), control characters, and account routes, preventing
 * open redirects through the login return path.
 */
export function safeReturnPath(
  candidate: unknown,
): string | null {
  if (typeof candidate !== "string") {
    return null;
  }

  if (
    candidate.length === 0 ||
    candidate.length > 2048 ||
    !candidate.startsWith("/") ||
    candidate.startsWith("//") ||
    candidate.includes("\\") ||
    // oxlint-disable-next-line no-control-regex
    /[\u0000-\u001f\u007f]/.test(candidate)
  ) {
    return null;
  }

  const pathname = candidate.split(/[?#]/, 1)[0];

  if (
    ACCOUNT_PATH_PREFIXES.some(
      (prefix) =>
        pathname === prefix ||
        pathname.startsWith(`${prefix}/`),
    )
  ) {
    return null;
  }

  return candidate;
}
