import { queryOptions } from "@tanstack/react-query";

import { getJson } from "./client";
import type { SetupStatus } from "../types/setup";


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
