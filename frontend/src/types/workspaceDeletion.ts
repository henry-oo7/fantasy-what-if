/** GET /api/admin/workspace/deletion */
export type WorkspaceDeletionStatus =
  | {
      status: "active";
      grace_period_days: number;
      confirmation_slug: string;
    }
  | {
      status: "deletion_pending";
      requested_at: string;
      scheduled_for: string;
      cancellable: boolean;
    };

/** POST /api/admin/workspace/deletion */
export interface WorkspaceDeletionRequest {
  current_password: string;
  confirmation_slug: string;
}

export interface WorkspaceDeletionRequestedResponse {
  detail: string;
  code: "workspace_deletion_requested";
  scheduled_for: string;
}

/** POST /api/admin/workspace/deletion/cancellation */
export interface WorkspaceDeletionCancellationRequest {
  current_password: string;
}

export interface WorkspaceDeletionCanceledResponse {
  detail: string;
  code: "workspace_deletion_canceled";
}
