import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { LaunchRunDialog } from "@/components/LaunchRun";
import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useLeaderboard, usePromptHistory, usePrompts } from "@/hooks/queries";
import {
  METRIC_COLUMNS,
  UNSCORED_CELL,
  formatRate,
  metricLabel,
} from "@/lib/format";
import { IOU_CHOICES, parseIouThreshold } from "@/lib/iou";
import { cn } from "@/lib/utils";
import type { LeaderboardRow } from "@/types";

// The Landing page (ADR 0011, spec §A.2/§B.3): every Result ranked best-first, filtered by
// Drawing and prompt and ranked by a metric — all mirrored into the URL so a board link is
// shareable. The service ranks; this screen renders, it does not re-rank.

/** The `?drawing_id=` value as a number, or `null` for "All drawings"/garbage. */
function parseDrawingId(raw: string | null): number | null {
  if (raw === null || raw === "") return null;
  const id = Number(raw);
  return Number.isInteger(id) ? id : null;
}

/** The `?prompt_family=` value, or `null` for "All prompts"/empty. */
function parsePromptFamily(raw: string | null): string | null {
  return raw === null || raw === "" ? null : raw;
}

/** The `?prompt_version=` value as a positive integer, or `null` for "All versions"/garbage. */
function parsePromptVersion(raw: string | null): number | null {
  if (raw === null || raw === "") return null;
  const version = Number(raw);
  return Number.isInteger(version) && version > 0 ? version : null;
}

export function Leaderboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const drawingId = parseDrawingId(searchParams.get("drawing_id"));
  const promptFamily = parsePromptFamily(searchParams.get("prompt_family"));
  // A version is only meaningful with a family (versions are per-family; the API 400s a
  // bare version), so a stray version without a family is ignored, not sent.
  const promptVersion = promptFamily
    ? parsePromptVersion(searchParams.get("prompt_version"))
    : null;
  const sort = searchParams.get("sort");
  const iouThreshold = parseIouThreshold(searchParams.get("iou_threshold"));

  const { data, isLoading, isError, error } = useLeaderboard({
    drawing_id: drawingId,
    prompt_family: promptFamily,
    prompt_version: promptVersion,
    sort,
    iou_threshold: iouThreshold,
  });

  // The family dropdown lists the prompt families; the version dropdown is populated from
  // the chosen family's history.
  const { data: promptsData } = usePrompts();
  const familyOptions = promptsData?.families ?? [];
  const { data: historyData } = usePromptHistory(
    promptFamily ?? "",
    promptFamily !== null,
  );
  const versionOptions = historyData?.versions ?? [];

  // Reflect a filter change into the URL query so the board is shareable and back/forward
  // work (spec §B.3). `null`/"all" drops the param so the server applies its default.
  function patchParams(patch: Record<string, string | null>) {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) next.delete(key);
      else next.set(key, value);
    }
    setSearchParams(next);
  }

  // The rank-by options + resolved sort come from the server (it already fell back an
  // unknown metric to the default), so the dropdown always mirrors the live board.
  const metrics = data?.metrics ?? [];
  const activeSort = data?.sort ?? "";

  // The board is exploratory whenever the server says the threshold in force is not the
  // canonical one. Read off the response rather than off `iouThreshold`, so the banner can
  // never claim an operating point the numbers were not actually computed at.
  const exploring = data ? !data.canonical_iou : false;

  return (
    <section className="mx-auto max-w-[1400px]">
      <header className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Leaderboard</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            Compare prompt + model configurations by score.
          </p>
        </div>
        <LaunchRunDialog />
      </header>

      {/* One left-aligned bar of filters — the Task tablist that used to sit opposite them
          is gone with the task itself (ADR 0032), and nothing replaces it. */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <label
          htmlFor="lb-drawing"
          className="text-xs font-medium text-muted-foreground"
        >
          Drawing
        </label>
        <select
          id="lb-drawing"
          value={drawingId ?? "all"}
          onChange={(e) =>
            patchParams({
              drawing_id: e.target.value === "all" ? null : e.target.value,
            })
          }
          className="rounded-md border bg-card px-2.5 py-1.5 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="all">All drawings</option>
          {data?.drawings.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>

        <label
          htmlFor="lb-prompt-family"
          className="ml-1 text-xs font-medium text-muted-foreground"
        >
          Prompt
        </label>
        <select
          id="lb-prompt-family"
          value={promptFamily ?? "all"}
          onChange={(e) =>
            // Changing family clears the version — versions are per-family (ticket 04).
            patchParams({
              prompt_family: e.target.value === "all" ? null : e.target.value,
              prompt_version: null,
            })
          }
          className="rounded-md border bg-card px-2.5 py-1.5 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="all">All prompts</option>
          {familyOptions.map((f) => (
            <option key={f.name} value={f.name}>
              {f.name}
            </option>
          ))}
        </select>

        <label
          htmlFor="lb-prompt-version"
          className="text-xs font-medium text-muted-foreground"
        >
          Version
        </label>
        <select
          id="lb-prompt-version"
          value={promptVersion ?? "all"}
          onChange={(e) =>
            patchParams({
              prompt_version: e.target.value === "all" ? null : e.target.value,
            })
          }
          disabled={promptFamily === null || versionOptions.length === 0}
          className="rounded-md border bg-card px-2.5 py-1.5 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
        >
          <option value="all">All versions</option>
          {versionOptions.map((v) => (
            <option key={v.version} value={v.version}>
              v{v.version}
            </option>
          ))}
        </select>

        <label
          htmlFor="lb-sort"
          className="ml-1 text-xs font-medium text-muted-foreground"
        >
          Rank by
        </label>
        <select
          id="lb-sort"
          value={activeSort}
          onChange={(e) => patchParams({ sort: e.target.value })}
          disabled={metrics.length === 0}
          className="rounded-md border bg-card px-2.5 py-1.5 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {metrics.map((m) => (
            <option key={m} value={m}>
              {metricLabel(m)}
            </option>
          ))}
        </select>

        <label
          htmlFor="lb-iou"
          className="ml-1 text-xs font-medium text-muted-foreground"
        >
          IoU
        </label>
        <select
          id="lb-iou"
          value={iouThreshold === null ? "" : String(iouThreshold)}
          onChange={(e) =>
            patchParams({ iou_threshold: e.target.value || null })
          }
          className="rounded-md border bg-card px-2.5 py-1.5 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="">Default</option>
          {IOU_CHOICES.map((t) => (
            <option key={t} value={t}>
              {t.toFixed(1)}
            </option>
          ))}
        </select>
      </div>

      {exploring ? (
        <div
          role="status"
          className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[13px]"
        >
          <span className="font-medium">
            Exploring at IoU {data?.iou_threshold.toFixed(2)}
          </span>
          <span className="text-muted-foreground">
            These rates are not the benchmark&apos;s published numbers and nothing
            was saved. The scored history is unchanged at IoU{" "}
            {data?.canonical_iou_threshold.toFixed(2)}.
          </span>
          <button
            type="button"
            onClick={() => patchParams({ iou_threshold: null })}
            className="ml-auto rounded-md border px-2 py-0.5 text-xs font-medium hover:bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Back to default
          </button>
        </div>
      ) : null}

      {isLoading ? (
        <LoadingBlock rows={8} />
      ) : isError ? (
        <ErrorBlock error={error} />
      ) : data && data.rows.length === 0 ? (
        <EmptyState
          title="No results yet — launch a run"
          description="Launch a run to see scored configurations here."
          action={<LaunchRunDialog />}
        />
      ) : (
        data && (
          <div className="overflow-x-auto rounded-lg border bg-card">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-14">Rank</TableHead>
                  <TableHead>Prompt</TableHead>
                  <TableHead>Model</TableHead>
                  {METRIC_COLUMNS.map((col) => (
                    <TableHead key={col.key} className="text-right">
                      {col.header}
                    </TableHead>
                  ))}
                  <TableHead>Drawing</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.rows.map((row) => (
                  <BoardRow
                    key={row.result_id}
                    row={row}
                    onOpen={() =>
                      navigate(
                        `/results/${row.result_id}` +
                          (iouThreshold !== null
                            ? `?iou_threshold=${iouThreshold}`
                            : ""),
                      )
                    }
                  />
                ))}
              </TableBody>
            </Table>
          </div>
        )
      )}
    </section>
  );
}

/** The metric cell text for one column, or `—` when the row is unscored (spec §C.2). */
function metricText(row: LeaderboardRow, key: string): string {
  if (!row.scored) return UNSCORED_CELL;
  switch (key) {
    case "precision":
      return formatRate(row.precision ?? 0);
    case "recall":
      return formatRate(row.recall ?? 0);
    case "f1":
      return formatRate(row.f1 ?? 0);
    default:
      return UNSCORED_CELL;
  }
}

function BoardRow({
  row,
  onOpen,
}: {
  row: LeaderboardRow;
  onOpen: () => void;
}) {
  const isLeader = row.rank === 1;
  return (
    <TableRow
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      tabIndex={0}
      role="button"
      aria-label={`Open result for ${row.model}`}
      className={cn(
        "cursor-pointer",
        !row.scored && "text-muted-foreground opacity-60",
      )}
    >
      <TableCell
        className={cn(
          "font-semibold",
          isLeader ? "text-success" : !row.scored && "text-muted-foreground",
        )}
      >
        {row.scored ? `#${row.rank}` : UNSCORED_CELL}
      </TableCell>
      <TableCell>
        <div className="font-medium text-foreground">{row.prompt_family}</div>
        <div className="font-mono text-[11.5px] text-muted-foreground">
          v{row.prompt_version}
        </div>
      </TableCell>
      <TableCell className="font-mono text-xs">{row.model}</TableCell>
      {METRIC_COLUMNS.map((col) => (
        <TableCell
          key={col.key}
          className={cn(
            "text-right font-mono text-[12.5px]",
            isLeader && col.emphasis
              ? "font-bold text-success"
              : row.scored && "text-foreground",
          )}
        >
          {metricText(row, col.key)}
        </TableCell>
      ))}
      <TableCell className="text-[12.5px] text-muted-foreground">
        <span className="inline-flex items-center gap-2">
          {row.drawing_name}
          {!row.scored && (
            <Link
              to={`/library/drawings/${row.drawing_id}#ground-truth`}
              onClick={(e) => e.stopPropagation()}
              className="font-semibold text-primary hover:underline"
            >
              + ground truth
            </Link>
          )}
        </span>
      </TableCell>
    </TableRow>
  );
}
