import { postJson } from "./client";
import type {
  ProviderValidationRequest,
  ProviderValidationResponse,
} from "../types/onboarding";

/**
 * Check that Fantasy What-If can read one ESPN league season.
 *
 * Deliberately not a React Query mutation: the mutation cache would retain
 * submitted credentials as variables. The body travels only in this POST.
 */
export function validateProviderAccess(
  request: ProviderValidationRequest,
  csrfToken: string | null,
): Promise<ProviderValidationResponse> {
  return postJson<ProviderValidationResponse>(
    "/onboarding/provider/validate",
    request,
    { csrfToken },
  );
}
