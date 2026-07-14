import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "@/api";
import type { CountingGtSaveRequest, LeaderboardParams } from "@/types";
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

/**
 * Delete a Run and everything under it (ADR-0016, ticket 07). On success the run history,
 * the Leaderboard (the Run's Results leave the board), and the shell's meta counts are all
 * stale, so each is invalidated; the caller routes back to the run history.
 */
export function useDeleteRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.deleteRun(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["leaderboard"] });
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

/** Prompt families grouped by Task for the Prompts list (spec §A.5). */
export function usePrompts() {
  return useQuery({ queryKey: ["prompts"], queryFn: api.prompts });
}

/**
 * Author a new prompt family's v1. On success the grouped list is stale, so it is
 * invalidated; the caller routes to the new family's history page (spec §A.5).
 */
export function useCreatePrompt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.createPrompt,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
    },
  });
}

/** One family's immutable version history, newest-first, keyed by its `(task, family)`
 * (spec §A.5). `enabled` lets the screen skip the fetch for an invalid `(task, family)`
 * path rather than firing a doomed request. */
export function usePromptHistory(
  task: string,
  family: string,
  enabled = true,
) {
  return useQuery({
    queryKey: ["prompt-history", task, family],
    queryFn: () => api.promptHistory(task, family),
    enabled,
  });
}

/**
 * "Edit" a family by appending the next immutable version (ADR 0009). On success both the
 * family's history and the grouped list (its latest version / count moved) are stale, so
 * both are invalidated (spec §A.5).
 */
export function useAppendPromptVersion(task: string, family: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => api.appendPromptVersion(task, family, text),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompt-history", task, family] });
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
    },
  });
}

/** The Library list of Drawings with page counts (spec §A.6). */
export function useDrawings() {
  return useQuery({ queryKey: ["drawings"], queryFn: api.drawings });
}

/**
 * Upload a PDF. On success the Drawings list and the shell's meta counts are stale, so both
 * are invalidated (a new Drawing also expands the launch form's options); the caller routes
 * to the new Drawing's detail (spec §A.6).
 */
export function useUploadDrawing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.uploadDrawing(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["drawings"] });
      queryClient.invalidateQueries({ queryKey: ["launch-options"] });
      queryClient.invalidateQueries({ queryKey: ["meta"] });
    },
  });
}

/** One Drawing's detail: its rendered Pages with dims + image URLs, keyed by id (spec §A.6).
 * `enabled` lets the screen skip the fetch for an invalid id rather than firing a doomed
 * request. */
export function useDrawing(id: number, enabled = true) {
  return useQuery({
    queryKey: ["drawing", id],
    queryFn: () => api.drawing(id),
    enabled,
  });
}

/**
 * Delete a Drawing and everything derived from it (ADR-0016, ticket 08). On success the
 * Drawings list, the run history (its Runs are gone), the Leaderboard (their Results leave
 * the board), the launch form's options (the Drawing drops out), and the shell's meta counts
 * are all stale, so each is invalidated; the caller routes back to the Drawings list.
 */
export function useDeleteDrawing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.deleteDrawing(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["drawings"] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["leaderboard"] });
      queryClient.invalidateQueries({ queryKey: ["launch-options"] });
      queryClient.invalidateQueries({ queryKey: ["meta"] });
    },
  });
}

/** The curated model catalog for the Library view (spec §A.6). */
export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: api.models });
}

/** One Drawing's counting-GT totals per taxonomy label, pre-filling the entry form (spec
 * §A.6). `enabled` skips the fetch for an invalid id, as the drawing detail does. */
export function useCountingGroundTruth(id: number, enabled = true) {
  return useQuery({
    queryKey: ["counting-gt", id],
    queryFn: () => api.countingGroundTruth(id),
    enabled,
  });
}

/**
 * Entering ground truth makes the previously-unscored Leaderboard rows and Result details
 * for this Drawing scored — with no re-run, since scores recompute on read (spec Further
 * Notes). So both are invalidated after a save/import; React Query re-fetches and the rows
 * flip to scored. Shared by the counting-save and COCO-import mutations.
 */
function invalidateScored(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ["leaderboard"] });
  queryClient.invalidateQueries({ queryKey: ["result"] });
}

/** Upsert one Drawing's counting-GT totals. On success the returned totals seed the
 * pre-fill cache and the now-scored board/results are invalidated (spec §A.6). */
export function useSaveCountingGroundTruth(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (totals: CountingGtSaveRequest) =>
      api.saveCountingGroundTruth(id, totals),
    onSuccess: (saved) => {
      queryClient.setQueryData(["counting-gt", id], saved);
      invalidateScored(queryClient);
    },
  });
}

/** Import one Drawing's LocationGroundTruth from a COCO upload. On success the now-scored
 * board/results are invalidated (spec §A.6); the caller renders the returned problem report. */
export function useImportLocationGroundTruth(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, labelMap }: { file: File; labelMap?: string }) =>
      api.importLocationGroundTruth(id, file, labelMap),
    onSuccess: () => {
      invalidateScored(queryClient);
    },
  });
}
