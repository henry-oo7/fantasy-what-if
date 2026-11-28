import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { personalQueryKeys } from "../api/personalLeagues";

export const POLL_INTERVAL_MS = 5_000;
export const MAX_POLL_ATTEMPTS = 120;
export const MAX_POLL_MS = 600_000;

/**
 * Bounded status polling for one Season. While `active` (queued or syncing)
 * it refetches that Season's status every 5 seconds, at most 120 times or 10 minutes. The
 * single interval lives in this effect only, so a Season change, the state
 * leaving queued/syncing, a restart, or unmount (logout and session expiry
 * unmount the layout) always clears it.
 *
 * `restart` opens a fresh window; mounting a layout always starts one.
 */
export function useSeasonPolling(seasonId: number | undefined, active: boolean) {
  const queryClient = useQueryClient();
  const [poll, setPoll] = useState({ runId: 0, exhaustedRun: -1, seasonId });
  // A window from another Season never counts.
  const current = poll.seasonId === seasonId ? poll : { runId: 0, exhaustedRun: -1 };

  useEffect(() => {
    if (seasonId === undefined || !active) {
      return;
    }

    const progress = { attempts: 0 };
    const started = Date.now();
    const runId = current.runId;
    const timer = setInterval(() => {
      progress.attempts += 1;
      void queryClient.refetchQueries({
        queryKey: personalQueryKeys.seasonStatus(seasonId),
        exact: true,
      });

      // Background tabs slow timers, so the wall clock bounds the window too.
      if (progress.attempts >= MAX_POLL_ATTEMPTS || Date.now() - started >= MAX_POLL_MS) {
        clearInterval(timer);
        setPoll({ runId, exhaustedRun: runId, seasonId });
      }
    }, POLL_INTERVAL_MS);

    return () => {
      clearInterval(timer);
    };
  }, [queryClient, seasonId, active, current.runId]);

  return {
    exhausted: active && current.exhaustedRun === current.runId,
    restart: () => {
      setPoll({ runId: current.runId + 1, exhaustedRun: -1, seasonId });
    },
  };
}
