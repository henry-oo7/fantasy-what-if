import { queryOptions } from "@tanstack/react-query";

import { getJson, postJson } from "./client";
import type {
  WorkspaceDeletionCanceledResponse,
  WorkspaceDeletionCancellationRequest,
  WorkspaceDeletionRequest,
  WorkspaceDeletionRequestedResponse,
  WorkspaceDeletionStatus,
} from "../types/workspaceDeletion";

const DELETION_PATH = "/admin/workspace/deletion";

/** Query keys carry no identity, slug, password, or token values. */
export const workspaceDeletionQueryKeys = {
  status: () => ["workspace-deletion", "status"] as const,
};

export function workspaceDeletionStatusQueryOptions() {
  return queryOptions({
    queryKey: workspaceDeletionQueryKeys.status(),
    queryFn: ({ signal }) =>
      getJson<WorkspaceDeletionStatus>(DELETION_PATH, signal),
  });
}

/**
 * Form submissions do not report errors globally: a wrong password,
 * mismatch, throttle, or unavailable outcome is shown in the form and
 * must not change global authentication state.
 */
export function requestWorkspaceDeletion(
  request: WorkspaceDeletionRequest,
  csrfToken: string,
): Promise<WorkspaceDeletionRequestedResponse> {
  return postJson<WorkspaceDeletionRequestedResponse>(DELETION_PATH, request, {
    csrfToken,
    reportErrors: false,
  });
}

export function cancelWorkspaceDeletion(
  request: WorkspaceDeletionCancellationRequest,
  csrfToken: string,
): Promise<WorkspaceDeletionCanceledResponse> {
  return postJson<WorkspaceDeletionCanceledResponse>(
    `${DELETION_PATH}/cancellation`,
    request,
    { csrfToken, reportErrors: false },
  );
}
