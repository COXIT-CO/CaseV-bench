import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ConfirmDeleteDialog } from "@/components/ConfirmDeleteDialog";
import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import { Button } from "@/components/ui/button";
import {
  useAppendPromptVersion,
  useDeletePromptFamily,
  useDeletePromptVersion,
  usePromptHistory,
} from "@/hooks/queries";
import { formatDate } from "@/lib/format";
import type { PromptHistoryResponse, PromptVersion, Task } from "@/types";

// A prompt family's immutable version history + the tuning loop (ADR 0011, spec §A.5/§B.2):
// every version listed newest-first, a side-by-side compare/read of any two versions (the
// loop the tool exists for), and an "edit" that appends the next immutable version
// (`POST …/versions`, ADR 0009) — prior versions are never mutated. The two seeded prompts
// (`default` per Task) land here too.

/** The two Tasks that can appear in the `/prompts/:task/:family` path. */
function parseTask(raw: string | undefined): Task | null {
  return raw === "counting" || raw === "location" ? raw : null;
}

export function PromptHistory() {
  const { task: rawTask, family: rawFamily } = useParams();
  const task = parseTask(rawTask);
  const family = rawFamily ?? "";

  const { data, isLoading, isError, error } = usePromptHistory(
    task ?? "",
    family,
    task !== null,
  );

  if (!task) {
    return (
      <Shell family={family}>
        <EmptyState
          title="Prompt not found"
          description="That task is not a valid prompt task."
        />
      </Shell>
    );
  }
  if (isLoading) {
    return (
      <Shell family={family}>
        <LoadingBlock rows={6} />
      </Shell>
    );
  }
  if (isError || !data) {
    return (
      <Shell family={family}>
        <ErrorBlock error={error} />
      </Shell>
    );
  }

  return (
    <Shell family={family}>
      <Header task={task} family={family} detail={data} />
      <CompareVersions versions={data.versions} />
      <VersionList task={task} family={family} versions={data.versions} />
      <EditForm task={task} family={family} latest={data.versions[0]} />
    </Shell>
  );
}

function Shell({
  family,
  children,
}: {
  family: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mx-auto max-w-[1280px]">
      <div className="mb-1.5 text-[12.5px] text-muted-foreground">
        <Link to="/prompts" className="hover:text-foreground">
          Prompts
        </Link>{" "}
        / {family}
      </div>
      {children}
    </section>
  );
}

function Header({
  task,
  family,
  detail,
}: {
  task: Task;
  family: string;
  detail: PromptHistoryResponse;
}) {
  const count = detail.versions.length;
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">
          {family}{" "}
          <span className="align-middle text-sm font-normal capitalize text-muted-foreground">
            {task}
          </span>
        </h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          {count} immutable version{count === 1 ? "" : "s"} — pick two to compare, or
          append a new one below.
        </p>
      </div>
      <DeleteFamilyButton
        task={task}
        family={family}
        runCount={detail.run_count}
        resultCount={detail.result_count}
      />
    </header>
  );
}

/** Delete this whole family and every Run pinning any of its versions, after a confirmation
 * that states the collateral (ADR-0016). On success the list/board/runs/meta are invalidated
 * and we route back to the Prompts list, since this history page no longer has a family. */
function DeleteFamilyButton({
  task,
  family,
  runCount,
  resultCount,
}: {
  task: Task;
  family: string;
  runCount: number;
  resultCount: number;
}) {
  const navigate = useNavigate();
  const deleteFamily = useDeletePromptFamily();

  return (
    <ConfirmDeleteDialog
      trigger={
        <Button variant="destructive" size="sm">
          Delete family
        </Button>
      }
      title={`Delete prompt family “${family}”?`}
      description={
        <>
          This permanently deletes “{family}” and all its versions, and the {runCount} run
          {runCount === 1 ? "" : "s"} / {resultCount} result
          {resultCount === 1 ? "" : "s"} that pinned any of them — removing them from the
          leaderboard.
        </>
      }
      confirmLabel="Delete family"
      pending={deleteFamily.isPending}
      error={deleteFamily.error}
      onConfirm={() =>
        deleteFamily.mutateAsync({ task, family }).then(() => navigate("/prompts"))
      }
    />
  );
}

const SELECT_CLASS =
  "rounded-md border bg-card px-2 py-1 text-[12.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** The compare/read view: two version pickers over the same history, each rendering that
 * version's full text side by side — the core tuning loop (spec §A.5). Defaults to the two
 * newest so an "edit → compare" lands on the change; a single-version family compares it
 * with itself (a plain read). */
function CompareVersions({ versions }: { versions: PromptVersion[] }) {
  const newest = versions[0].version;
  const previous = versions[1]?.version ?? newest;
  const [left, setLeft] = React.useState(previous);
  const [right, setRight] = React.useState(newest);

  // A newly appended version becomes the right-hand default so the just-made change shows.
  React.useEffect(() => {
    setRight(newest);
    setLeft(versions[1]?.version ?? newest);
  }, [newest, versions]);

  return (
    <div className="mb-8" role="region" aria-label="Compare versions">
      <h2 className="mb-2.5 text-sm font-semibold">Compare versions</h2>
      <div className="grid gap-4 md:grid-cols-2">
        <VersionPane
          side="left"
          versions={versions}
          selected={left}
          onSelect={setLeft}
        />
        <VersionPane
          side="right"
          versions={versions}
          selected={right}
          onSelect={setRight}
        />
      </div>
    </div>
  );
}

function VersionPane({
  side,
  versions,
  selected,
  onSelect,
}: {
  side: "left" | "right";
  versions: PromptVersion[];
  selected: number;
  onSelect: (version: number) => void;
}) {
  const version = versions.find((v) => v.version === selected) ?? versions[0];
  const label = side === "left" ? "Version A" : "Version B";
  const selectId = `compare-${side}`;
  return (
    <div className="flex min-w-0 flex-col rounded-lg border bg-card">
      <div className="flex items-center justify-between gap-3 border-b px-3 py-2">
        <label
          htmlFor={selectId}
          className="text-xs font-semibold text-muted-foreground"
        >
          {label}
        </label>
        <div className="flex items-center gap-2">
          <span className="text-[11.5px] text-muted-foreground">
            {formatDate(version.created_at)}
          </span>
          <select
            id={selectId}
            aria-label={label}
            className={SELECT_CLASS}
            value={selected}
            onChange={(e) => onSelect(Number(e.target.value))}
          >
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version}
              </option>
            ))}
          </select>
        </div>
      </div>
      <pre className="overflow-x-auto whitespace-pre-wrap px-3.5 py-3 font-mono text-[12px] leading-relaxed">
        {version.text}
      </pre>
    </div>
  );
}

/** The version roster: every immutable version listed newest-first, each deletable on its own
 * (ADR-0016, ticket 09). A mid-lineage delete leaves a cosmetic numbering gap (v1, v3) — the
 * remaining history is otherwise untouched. Each row states how many Runs pinned that version,
 * the collateral its delete cascades. */
function VersionList({
  task,
  family,
  versions,
}: {
  task: Task;
  family: string;
  versions: PromptVersion[];
}) {
  return (
    <div className="mb-8" role="region" aria-label="Versions">
      <h2 className="mb-2.5 text-sm font-semibold">Versions</h2>
      <div className="flex flex-col gap-2">
        {versions.map((version) => (
          <div
            key={version.version}
            className="flex items-center justify-between gap-4 rounded-lg border bg-card px-3.5 py-3"
          >
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-[13px] font-medium">
                v{version.version}
              </span>
              <span className="text-[11.5px] text-muted-foreground">
                {formatDate(version.created_at)}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-[12px] text-muted-foreground">
                {version.run_count} run{version.run_count === 1 ? "" : "s"} pinned
              </span>
              <DeleteVersionButton
                task={task}
                family={family}
                version={version}
                isLastVersion={versions.length === 1}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Delete one version and the Runs that pinned it, after a confirmation that states the
 * collateral (ADR-0016). On success the history refetches in place; but deleting a family's
 * only version leaves nothing to show, so we route back to the Prompts list in that case. */
function DeleteVersionButton({
  task,
  family,
  version,
  isLastVersion,
}: {
  task: Task;
  family: string;
  version: PromptVersion;
  isLastVersion: boolean;
}) {
  const navigate = useNavigate();
  const deleteVersion = useDeletePromptVersion(task, family);

  return (
    <ConfirmDeleteDialog
      trigger={
        <Button variant="destructive" size="sm">
          Delete v{version.version}
        </Button>
      }
      title={`Delete “${family}” v${version.version}?`}
      description={
        <>
          This permanently deletes version {version.version} of “{family}” and the{" "}
          {version.run_count} run{version.run_count === 1 ? "" : "s"} /{" "}
          {version.result_count} result{version.result_count === 1 ? "" : "s"} that pinned
          it — removing them from the leaderboard.
        </>
      }
      confirmLabel={`Delete v${version.version}`}
      pending={deleteVersion.isPending}
      error={deleteVersion.error}
      onConfirm={() =>
        deleteVersion.mutateAsync(version.version).then(() => {
          if (isLastVersion) navigate("/prompts");
        })
      }
    />
  );
}

/** The "edit" that appends the next immutable version (ADR 0009). Prefilled from the latest
 * text so a tweak starts from where the family is; submitting never mutates a prior
 * version (spec §A.5). */
function EditForm({
  task,
  family,
  latest,
}: {
  task: Task;
  family: string;
  latest: PromptVersion;
}) {
  const [text, setText] = React.useState(latest.text);
  const append = useAppendPromptVersion(task, family);

  // When a new version lands, re-baseline the draft on it so the next edit builds forward.
  React.useEffect(() => {
    setText(latest.text);
  }, [latest.version, latest.text]);

  const changed = text.trim().length > 0 && text !== latest.text;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!changed || append.isPending) return;
    append.mutate(text);
  }

  return (
    <form onSubmit={submit} className="rounded-lg border bg-card p-4">
      <div className="mb-1 text-sm font-semibold">
        Edit prompt
        <span className="ml-2 align-middle font-mono text-[11.5px] font-normal text-muted-foreground">
          from v{latest.version}
        </span>
      </div>
      <p className="mb-3 text-[11.5px] text-muted-foreground">
        Saving creates a new immutable version under this family — it never changes an
        existing one.
      </p>
      <textarea
        aria-label="Prompt text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={10}
        className="w-full resize-y rounded-md border bg-background px-2.5 py-2 font-mono text-[12px] leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
      {append.isError && <ErrorBlock className="mt-3" error={append.error} />}
      <div className="mt-3 flex items-center gap-3">
        <Button type="submit" disabled={!changed || append.isPending}>
          {append.isPending ? "Saving…" : "Save as new version"}
        </Button>
        {!changed && (
          <span className="text-[11.5px] text-muted-foreground">
            Edit the text to save a new version.
          </span>
        )}
      </div>
    </form>
  );
}
