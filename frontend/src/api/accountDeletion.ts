import { postJson } from "./client";

export interface AccountDeletionRequest {
  current_password: string;
  confirmation: string;
}

export interface AccountDeletedResponse {
  detail: string;
  code: "account_deleted";
}

/**
 * Immediate, confirmed deletion of the signed-in personal account. Outcomes
 * are shown in the form and must not change global authentication state.
 */
export function deleteAccount(
  request: AccountDeletionRequest,
  csrfToken: string,
): Promise<AccountDeletedResponse> {
  return postJson<AccountDeletedResponse>("/account/deletion", request, {
    csrfToken,
    reportErrors: false,
  });
}
