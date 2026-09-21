import * as React from "react";
import { Link, useNavigate } from "react-router-dom";

import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import { Button } from "@/components/ui/button";
import { useCreatePrompt, usePrompts } from "@/hooks/queries";
import type { PromptFamily } from "@/types";

// The Prompts list + authoring form (ADR 0011, spec §A.5/§B.2): every prompt family in a
// flat list, each showing its latest version and version count and linking to its immutable
// history. Authoring a prompt creates a new family at v1 (`POST /api/prompts`) — "editing"
// appends a version, which lives on the history page. On create the SPA routes to the new
// family's history to keep tuning.

export function Prompts() {
  const { data, isLoading, isError, error } = usePrompts();

  return (
    <section className="mx-auto max-w-[1280px]">
      <header className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight">Prompts</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Author a prompt, then tune it — every edit appends a new immutable version a run
          can pin.
        </p>
      </header>

      {isLoading ? (
        <LoadingBlock rows={6} />
      ) : isError || !data ? (
        <ErrorBlock error={error} />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
          <FamilyList families={data.families} />
          <CreatePromptCard />
        </div>
      )}
    </section>
  );
}

/** Every prompt family in one flat list, each linking to its immutable history (spec §A.5). */
function FamilyList({ families }: { families: PromptFamily[] }) {
  if (families.length === 0) {
    return (
      <EmptyState
        title="No prompts yet"
        description="Author your first prompt with the form to start a tuning loop."
      />
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {families.map((family) => (
        <Link
          key={family.name}
          to={`/prompts/${encodeURIComponent(family.name)}`}
          className="flex items-center justify-between gap-4 rounded-lg border bg-card px-3.5 py-3 transition-colors hover:border-primary/50"
        >
          <span className="font-medium">{family.name}</span>
          <span className="flex items-center gap-3 text-[12px] text-muted-foreground">
            <span className="font-mono">latest v{family.latest_version}</span>
            <span>
              {family.count} version{family.count === 1 ? "" : "s"}
            </span>
          </span>
        </Link>
      ))}
    </div>
  );
}

const FIELD_CLASS =
  "w-full rounded-md border bg-card px-2.5 py-2 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** The authoring form: Family + Prompt text create a new family's v1 (spec §A.5). A
 * duplicate family surfaces the service's `400` message inline. */
function CreatePromptCard() {
  const navigate = useNavigate();
  const [family, setFamily] = React.useState("");
  const [text, setText] = React.useState("");
  const createPrompt = useCreatePrompt();

  const canSubmit =
    family.trim().length > 0 && text.trim().length > 0 && !createPrompt.isPending;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    createPrompt.mutate(
      { family: family.trim(), text },
      {
        onSuccess: (ref) => navigate(`/prompts/${encodeURIComponent(ref.family)}`),
      },
    );
  }

  return (
    <form
      onSubmit={submit}
      className="flex h-fit flex-col gap-4 rounded-lg border bg-card p-4"
    >
      <div className="text-sm font-semibold">New prompt</div>

      <Field label="Family" htmlFor="prompt-family">
        <input
          id="prompt-family"
          type="text"
          value={family}
          onChange={(e) => setFamily(e.target.value)}
          placeholder="e.g. cabinet-boxes-v2"
          className={FIELD_CLASS}
        />
      </Field>

      <Field label="Prompt text" htmlFor="prompt-text">
        <textarea
          id="prompt-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={8}
          placeholder="Instruction text sent to the model…"
          className={`${FIELD_CLASS} resize-y font-mono text-[12px] leading-relaxed`}
        />
      </Field>

      {createPrompt.isError && <ErrorBlock error={createPrompt.error} />}

      <p className="text-[11.5px] text-muted-foreground">
        Creates version 1 of a new family. Editing later appends a new immutable version.
      </p>
      <Button type="submit" disabled={!canSubmit}>
        {createPrompt.isPending ? "Creating…" : "Create prompt"}
      </Button>
    </form>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        className="mb-1.5 block text-xs font-semibold text-muted-foreground"
      >
        {label}
      </label>
      {children}
    </div>
  );
}
