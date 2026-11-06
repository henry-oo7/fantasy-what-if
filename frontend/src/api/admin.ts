import {
  queryOptions,
} from "@tanstack/react-query";

import {
  getJson,
  postJson,
} from "./client";
import type {
  CreateInvitationRequest,
  CreatedInvitation,
  InvitationListResponse,
  RevokedInvitation,
} from "../types/admin";


export const adminQueryKeys = {
  all: ["admin"] as const,

  invitations: () =>
    [
      ...adminQueryKeys.all,
      "invitations",
    ] as const,
};

export function invitationListQueryOptions() {
  return queryOptions({
    queryKey: adminQueryKeys.invitations(),
    queryFn: ({ signal }) =>
      getJson<InvitationListResponse>(
        "/admin/invitations",
        signal,
      ),
    staleTime: 30_000,
  });
}

export function createInvitation(
  request: CreateInvitationRequest,
  csrfToken: string,
): Promise<CreatedInvitation> {
  return postJson<CreatedInvitation>(
    "/admin/invitations",
    request,
    {
      csrfToken,
    },
  );
}

export function revokeInvitation(
  invitationId: number,
  csrfToken: string,
): Promise<RevokedInvitation> {
  return postJson<RevokedInvitation>(
    `/admin/invitations/${invitationId}/revoke`,
    {},
    {
      csrfToken,
    },
  );
}
