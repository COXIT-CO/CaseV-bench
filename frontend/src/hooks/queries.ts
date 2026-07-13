import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "@/api";
import type { LeaderboardParams } from "@/types";
import { isTerminalRunStatus } from "@/types";

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

/** One Result's drill-down, keyed by its id (spec §A.3). */
export function useResult(id: number) {
  return useQuery({
    queryKey: ["result", id],
    queryFn: () => api.result(id),
  });
}

/** The run history list (spec §A.4). */
export function useRuns() {
  return useQuery({ queryKey: ["runs"], queryFn: api.runs });
}

/** The launch form's option set (prompts, drawings, catalog) (spec §A.4). */
export function useLaunchOptions() {
  return useQuery({ queryKey: ["launch-options"], queryFn: api.launchOptions });
}

/**
 * Launch a Run. On success the run history and the shell's meta counts are stale, so both
 * are invalidated; the caller routes to the new run's detail page (spec §A.4).
 */
export function useCreateRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.createRun,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["meta"] });
    },
  });
}

/** One Run's static detail: header, fixed-knobs snapshot, result rows (spec §A.4). */
export function useRun(id: number) {
  return useQuery({
    queryKey: ["run", id],
    queryFn: () => api.run(id),
  });
}

/**
 * The live run status, polled every second while non-terminal and stopping at
 * `done`/`failed` — the React-side replacement for HTMX's `hx-trigger` poll (spec §B.4,
 * ADR 0006/0010). `refetchInterval` returns `false` once terminal so the poll halts.
 */
export function useRunStatus(id: number) {
  return useQuery({
    queryKey: ["run-status", id],
    queryFn: () => api.runStatus(id),
    refetchInterval: (query) =>
      isTerminalRunStatus(query.state.data?.status) ? false : 1500,
  });
}
