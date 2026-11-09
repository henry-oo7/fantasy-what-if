import {
  queryOptions,
} from "@tanstack/react-query";

import {
  getJson,
  postJson,
} from "./client";
import type {
  BootstrapAdminRequest,
  BootstrapAdminResponse,
  SetupStatus,
} from "../types/setup";


export const setupQueryKeys = {
  all: ["setup"] as const,

  status: () =>
    [
      ...setupQueryKeys.all,
      "status",
    ] as const,
};


export function setupStatusQueryOptions() {
  return queryOptions({
    queryKey: setupQueryKeys.status(),
    queryFn: ({ signal }) =>
      getJson<SetupStatus>(
        "/setup/status",
        signal,
      ),
    staleTime: 30_000,
    retry: false,
  });
}


export function bootstrapAdministrator(
  request: BootstrapAdminRequest,
  csrfToken: string,
): Promise<BootstrapAdminResponse> {
  return postJson<BootstrapAdminResponse>(
    "/setup/bootstrap-admin",
    request,
    {
      csrfToken,
    },
  );
}


export function setupProvidersQueryOptions() {
  return queryOptions({
    queryKey: [
      ...setupQueryKeys.all,
      "providers",
    ] as const,
    queryFn: ({ signal }) =>
      getJson<
        import(
          "../types/setup"
        ).SetupProvidersResponse
      >(
        "/setup/providers",
        signal,
      ),
    staleTime: 300_000,
  });
}


export function createProviderConnection(
  request: import(
    "../types/setup"
  ).ProviderConnectionRequest,
  csrfToken: string,
): Promise<
  import(
    "../types/setup"
  ).ProviderConnectionResponse
> {
  return postJson(
    "/setup/provider-connection",
    request,
    {
      csrfToken,
    },
  );
}


export function finalizeSetup(
  csrfToken: string,
): Promise<
  import(
    "../types/setup"
  ).SetupFinalizationResponse
> {
  return postJson(
    "/setup/finalize",
    {},
    {
      csrfToken,
    },
  );
}
