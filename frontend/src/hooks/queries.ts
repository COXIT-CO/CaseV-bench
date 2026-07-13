import { useQuery } from "@tanstack/react-query";

import { api } from "@/api";
import type { LeaderboardParams } from "@/types";

// React Query hooks keyed per Part-A endpoint. Slice 0 has just the proof query; feature
// hooks (leaderboard, result, runs, …) are added by their own slices.

export function useMeta() {
  return useQuery({ queryKey: ["meta"], queryFn: api.meta });
}

/**
 * The Leaderboard board for the current filter set. Keyed by the exact filters (spec
 * §B.3) so the same URL is cached and shared; `placeholderData: (prev) => prev` keeps the
 * old board visible while a Task/Drawing/sort switch refetches, avoiding a flash to
 * skeleton (React Query v5's replacement for v4's `keepPreviousData`).
 */
export function useLeaderboard(params: LeaderboardParams) {
  return useQuery({
    queryKey: ["leaderboard", params.task, params.drawing_id, params.sort],
    queryFn: () => api.leaderboard(params),
    placeholderData: (prev) => prev,
  });
}
