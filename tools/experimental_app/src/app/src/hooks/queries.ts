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
 * old board visible while a Drawing/prompt/sort switch refetches, avoiding a flash to
 * skeleton (React Query v5's replacement for v4's `keepPreviousData`).
 */
export function useLeaderboard(params: LeaderboardParams) {
  return useQuery({
    queryKey: [
      "leaderboard",
      params.drawing_id,
      params.prompt_family,
      params.prompt_version,
      params.sort,
      params.iou_threshold,
    ],
    queryFn: () => api.leaderboard(params),
    placeholderData: (prev) => prev,
  });
}

/** One Result's drill-down, keyed by its id (spec §A.3). */
export function useResult(id: number, iouThreshold: number | null = null) {
  return useQuery({
    queryKey: ["result", id, iouThreshold],
    queryFn: () => api.result(id, iouThreshold),
  });
}

/**
 * Set a location Prediction's manual JSON override (ADR 0020, ticket 07). On success only this
 * Result's drill-down is stale, so it is invalidated — the overlay redraws and the "edited"
 * badge appears. The Leaderboard is deliberately **not** invalidated: the edit never touches the
 * Score, so the board is unmoved.
 */
export function useSetPredictionOverride(resultId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      pageNumber,
      editedJson,
    }: {
      pageNumber: number;
      editedJson: string;
    }) => api.setPredictionOverride(resultId, pageNumber, editedJson),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["result", resultId] });
    },
  });
}

/**
 * Revert a location Prediction to the model's output (ADR 0020, ticket 07). On success this
 * Result's drill-down is invalidated so the original overlay and JSON return; the Leaderboard
 * is untouched (the Score never changed).
 */
export function useRevertPredictionOverride(resultId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (pageNumber: number) =>
      api.revertPredictionOverride(resultId, pageNumber),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["result", resultId] });
    },
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

/** Every prompt family for the Prompts list (spec §A.5). */
export function usePrompts() {
  return useQuery({ queryKey: ["prompts"], queryFn: api.prompts });
}

/**
 * Author a new prompt family's v1. On success the family list is stale, so it is
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

/** One family's immutable version history, newest-first, keyed by its `family`
 * (spec §A.5). `enabled` lets a screen with no family chosen — the Leaderboard's version
 * filter before a family is picked — skip the fetch rather than fire a doomed request. */
export function usePromptHistory(family: string, enabled = true) {
  return useQuery({
    queryKey: ["prompt-history", family],
    queryFn: () => api.promptHistory(family),
    enabled,
  });
}

/**
 * "Edit" a family by appending the next immutable version (ADR 0009). On success both the
 * family's history and the family list (its latest version / count moved) are stale, so
 * both are invalidated (spec §A.5).
 */
export function useAppendPromptVersion(family: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => api.appendPromptVersion(family, text),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompt-history", family] });
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
    },
  });
}

/**
 * Delete one immutable version and cascade the Runs that pinned it (ADR-0016, ticket 09). On
 * success the family's history, the family list (its latest version / count moved, or the
 * family vanished if that was its last version), the run history, the Leaderboard (the pinned
 * Runs' Results leave the board), and the shell's meta counts are all stale, so each is
 * invalidated. The caller decides where to route (stay, or back to the list if the family is
 * now empty).
 */
export function useDeletePromptVersion(family: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (version: number) => api.deletePromptVersion(family, version),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompt-history", family] });
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["leaderboard"] });
      queryClient.invalidateQueries({ queryKey: ["meta"] });
    },
  });
}

/**
 * Delete a whole prompt family — every version and every Run pinning any of them (ADR-0016,
 * ticket 09). On success the family list, the run history, the Leaderboard, and the shell's
 * meta counts are all stale, so each is invalidated; the caller routes back to the Prompts
 * list, since the family's history page no longer has a family to show.
 */
export function useDeletePromptFamily() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (family: string) => api.deletePromptFamily(family),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["leaderboard"] });
      queryClient.invalidateQueries({ queryKey: ["meta"] });
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

/** The user-editable model catalog for the Library view (spec §A.6, ticket 10). */
export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: api.models });
}

/**
 * Add a model to the catalog, or re-label a known slug (upsert, ticket 10). On success the
 * catalog view and the launch form's options (the entry becomes a labeled checkbox on every
 * future launch) are both stale, so each is invalidated.
 */
export function useAddModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.addModel,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["launch-options"] });
    },
  });
}

/**
 * Remove a model from the catalog by slug (ticket 10). Safe by construction — past Runs store
 * the slug string, not a reference, so nothing else is touched. On success the catalog view
 * and the launch form's options (the checkbox drops out) are both stale, so each is invalidated.
 */
export function useRemoveModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => api.removeModel(slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["launch-options"] });
    },
  });
}

/**
 * Import one Drawing's LocationGroundTruth from a native `objects` upload (spec §A.6); the
 * caller renders the returned problem report.
 *
 * Ground truth makes the previously-unscored Leaderboard rows and Result details for this
 * Drawing scored — with no re-run, since scores recompute on read (spec Further Notes). So
 * both are invalidated on success; React Query re-fetches and the rows flip to scored.
 */
export function useImportLocationGroundTruth(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.importLocationGroundTruth(id, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leaderboard"] });
      queryClient.invalidateQueries({ queryKey: ["result"] });
    },
  });
}
