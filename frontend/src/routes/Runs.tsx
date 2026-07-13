import { useNavigate } from "react-router-dom";

import { LaunchRunDialog } from "@/components/LaunchRun";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useRuns } from "@/hooks/queries";
import type { RunListItem } from "@/types";

// The Runs history (ADR 0011, spec §A.4/§B.2): every launched Run listed newest-first with
// its task, prompt, drawing, status, and `progress / total` counter — each row opening its
// detail page. "Launch run" opens the shared launch Dialog (also the Leaderboard's CTA).

export function Runs() {
  const { data, isLoading, isError, error } = useRuns();

  return (
    <section className="mx-auto max-w-[1280px]">
      <header className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Runs</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            Launch a run, then watch it execute and score.
          </p>
        </div>
        <LaunchRunDialog />
      </header>

      {isLoading ? (
        <LoadingBlock rows={6} />
      ) : isError ? (
        <ErrorBlock error={error} />
      ) : data && data.runs.length === 0 ? (
        <EmptyState
          title="No runs yet"
          description="Launch a run to compare prompt + model configurations."
          action={<LaunchRunDialog />}
        />
      ) : (
        data && <RunTable runs={data.runs} />
      )}
    </section>
  );
}

function RunTable({ runs }: { runs: RunListItem[] }) {
  const navigate = useNavigate();
  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="w-20">Run</TableHead>
            <TableHead>Task</TableHead>
            <TableHead>Prompt</TableHead>
            <TableHead>Drawing</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Progress</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((run) => (
            <TableRow
              key={run.id}
              onClick={() => navigate(`/runs/${run.id}`)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  navigate(`/runs/${run.id}`);
                }
              }}
              tabIndex={0}
              role="button"
              aria-label={`Open run ${run.id}`}
              className="cursor-pointer"
            >
              <TableCell className="font-mono text-xs">#{run.id}</TableCell>
              <TableCell className="capitalize">{run.task}</TableCell>
              <TableCell>
                <span className="font-medium">{run.prompt_family}</span>{" "}
                <span className="font-mono text-[11.5px] text-muted-foreground">
                  v{run.prompt_version}
                </span>
              </TableCell>
              <TableCell className="text-[12.5px] text-muted-foreground">
                {run.drawing_name}
              </TableCell>
              <TableCell>
                <StatusBadge status={run.status} />
              </TableCell>
              <TableCell className="text-right font-mono text-[12.5px] text-muted-foreground">
                {run.progress} / {run.total_units}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
