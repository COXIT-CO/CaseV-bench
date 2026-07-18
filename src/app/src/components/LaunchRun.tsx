import * as React from "react";
import { Link, useNavigate } from "react-router-dom";

import { ErrorBlock, LoadingBlock } from "@/components/states";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useCreateRun, useLaunchOptions } from "@/hooks/queries";
import { cn } from "@/lib/utils";
import type { CatalogEntry, LaunchOptionsResponse } from "@/types";

// The launch→watch loop's entry point (spec §A.4, §B.2). One `LaunchRunForm` reused as a
// Dialog wired to the Leaderboard's "Launch run" CTA and to the Runs page. The server
// resolves the final slug list (curated + free-text) on launch, so the form just gathers
// intent; on success the SPA routes to the new run's detail page to watch it execute.

/** The "Launch run" button + Dialog wrapper, reused across screens (spec §B.2). */
export function LaunchRunDialog() {
  const [open, setOpen] = React.useState(false);
  const navigate = useNavigate();

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">Launch run</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Launch a run</DialogTitle>
          <DialogDescription>
            Pick a prompt version, a drawing, and one or more models.
          </DialogDescription>
        </DialogHeader>
        <LaunchRunForm
          onLaunched={(id) => {
            setOpen(false);
            navigate(`/runs/${id}`);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

/** The launch form itself: prompt + drawing selects, a model multi-select, and submit.
 * Guides the developer to create a prompt/drawing when either set is empty (spec §A.4). */
export function LaunchRunForm({
  onLaunched,
}: {
  onLaunched: (runId: number) => void;
}) {
  const { data, isLoading, isError, error } = useLaunchOptions();

  if (isLoading) return <LoadingBlock rows={4} />;
  if (isError || !data) return <ErrorBlock error={error} />;
  if (data.prompts.length === 0 || data.drawings.length === 0) {
    return <MissingPrerequisites options={data} />;
  }
  return <LaunchRunFields options={data} onLaunched={onLaunched} />;
}

/** The no-dead-form guard: point the developer at whatever they're missing (spec §A.4). */
function MissingPrerequisites({ options }: { options: LaunchOptionsResponse }) {
  const needsPrompt = options.prompts.length === 0;
  const needsDrawing = options.drawings.length === 0;
  return (
    <div className="rounded-lg border border-dashed p-6 text-center">
      <p className="text-sm font-medium">Nothing to run yet</p>
      <p className="mx-auto mt-1.5 max-w-sm text-[13px] text-muted-foreground">
        A run needs at least one prompt and one uploaded drawing.
      </p>
      <div className="mt-4 flex justify-center gap-2">
        {needsPrompt && (
          <Button asChild size="sm">
            <Link to="/prompts">Create a prompt</Link>
          </Button>
        )}
        {needsDrawing && (
          <Button asChild size="sm" variant={needsPrompt ? "outline" : "default"}>
            <Link to="/library/drawings">Upload a drawing</Link>
          </Button>
        )}
      </div>
    </div>
  );
}

const SELECT_CLASS =
  "w-full rounded-md border bg-card px-2.5 py-2 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

// The Advanced knobs' pre-filled defaults — the common one-click launch keeps these, so the
// form matches the server's `RunKnobs` defaults (ticket 04, ADR 0018).
const DEFAULT_MAX_TOKENS = 4096;
const DEFAULT_TEMPERATURE = 0;

function LaunchRunFields({
  options,
  onLaunched,
}: {
  options: LaunchOptionsResponse;
  onLaunched: (runId: number) => void;
}) {
  const [promptId, setPromptId] = React.useState(options.prompts[0].id);
  const [drawingId, setDrawingId] = React.useState(options.drawings[0].id);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [freeText, setFreeText] = React.useState("");
  // The Advanced knobs, pre-filled and collapsed. `providerDefault` sends `temperature: null`
  // so a reasoning Model that rejects an explicit temperature still runs under this config.
  const [maxTokens, setMaxTokens] = React.useState(DEFAULT_MAX_TOKENS);
  const [temperature, setTemperature] = React.useState(DEFAULT_TEMPERATURE);
  const [providerDefault, setProviderDefault] = React.useState(false);
  const createRun = useCreateRun();

  function toggle(slug: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  // At least one model is required; the curated selection and the free-text hatch both
  // count. The server re-resolves the slug list as the source of truth on launch.
  const hasModel = selected.size > 0 || freeText.trim().length > 0;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!hasModel || createRun.isPending) return;
    createRun.mutate(
      {
        prompt_id: promptId,
        drawing_id: drawingId,
        models: [...selected],
        free_text: freeText,
        max_tokens: maxTokens,
        // "Provider default" → null, which the server omits from the request payload.
        temperature: providerDefault ? null : temperature,
      },
      { onSuccess: (run) => onLaunched(run.id) },
    );
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Field label="Prompt version" htmlFor="launch-prompt">
        <select
          id="launch-prompt"
          className={SELECT_CLASS}
          value={promptId}
          onChange={(e) => setPromptId(Number(e.target.value))}
        >
          {options.prompts.map((p) => (
            <option key={p.id} value={p.id}>
              {p.task}: {p.family} — v{p.version}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Drawing" htmlFor="launch-drawing">
        <select
          id="launch-drawing"
          className={SELECT_CLASS}
          value={drawingId}
          onChange={(e) => setDrawingId(Number(e.target.value))}
        >
          {options.drawings.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} ({d.page_count} page{d.page_count === 1 ? "" : "s"})
            </option>
          ))}
        </select>
      </Field>

      <Field label="Models">
        <div className="flex flex-wrap gap-1.5">
          {options.catalog.map((entry) => (
            <ModelChip
              key={entry.slug}
              entry={entry}
              selected={selected.has(entry.slug)}
              onToggle={() => toggle(entry.slug)}
            />
          ))}
        </div>
        <input
          type="text"
          value={freeText}
          onChange={(e) => setFreeText(e.target.value)}
          placeholder="or paste OpenRouter slug(s), comma/space separated…"
          className="mt-2 w-full rounded-md border bg-card px-2.5 py-2 font-mono text-[12px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </Field>

      <AdvancedKnobs
        maxTokens={maxTokens}
        onMaxTokens={setMaxTokens}
        temperature={temperature}
        onTemperature={setTemperature}
        providerDefault={providerDefault}
        onProviderDefault={setProviderDefault}
      />

      {createRun.isError && <ErrorBlock error={createRun.error} />}

      <Button type="submit" disabled={!hasModel || createRun.isPending}>
        {createRun.isPending ? "Launching…" : "Launch run"}
      </Button>
    </form>
  );
}

const KNOB_INPUT_CLASS =
  "w-full rounded-md border bg-card px-2.5 py-2 font-mono text-[12px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

/** The collapsed Advanced section: `max_tokens` and temperature, pre-filled with the common
 * defaults so the one-click launch is unchanged (ticket 04). Temperature is a number, or
 * "provider default" (→ `null`, omitted from the request) for Models that reject an explicit
 * temperature. DPI/downsample land here in a later slice. */
function AdvancedKnobs({
  maxTokens,
  onMaxTokens,
  temperature,
  onTemperature,
  providerDefault,
  onProviderDefault,
}: {
  maxTokens: number;
  onMaxTokens: (value: number) => void;
  temperature: number;
  onTemperature: (value: number) => void;
  providerDefault: boolean;
  onProviderDefault: (value: boolean) => void;
}) {
  return (
    <details className="rounded-md border bg-card px-3 py-2">
      <summary className="cursor-pointer text-xs font-semibold text-muted-foreground">
        Advanced
      </summary>
      <div className="mt-3 flex flex-col gap-4">
        <Field label="max_tokens" htmlFor="launch-max-tokens">
          <input
            id="launch-max-tokens"
            type="number"
            min={1}
            step={1}
            value={maxTokens}
            onChange={(e) => onMaxTokens(Number(e.target.value))}
            className={KNOB_INPUT_CLASS}
          />
        </Field>

        <Field label="temperature" htmlFor="launch-temperature">
          <input
            id="launch-temperature"
            type="number"
            min={0}
            step="0.1"
            value={providerDefault ? "" : temperature}
            disabled={providerDefault}
            onChange={(e) => onTemperature(Number(e.target.value))}
            className={KNOB_INPUT_CLASS}
          />
          <label className="mt-2 flex items-center gap-2 text-[12px] text-muted-foreground">
            <input
              type="checkbox"
              checked={providerDefault}
              onChange={(e) => onProviderDefault(e.target.checked)}
            />
            Use provider default (omit temperature)
          </label>
        </Field>
      </div>
    </details>
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

function ModelChip({
  entry,
  selected,
  onToggle,
}: {
  entry: CatalogEntry;
  selected: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onToggle}
      title={entry.slug}
      className={cn(
        "rounded-md border px-2.5 py-1 font-mono text-[11.5px] transition-colors",
        selected
          ? "border-primary bg-primary/10 text-primary"
          : "text-muted-foreground hover:border-primary/50 hover:text-foreground",
      )}
    >
      {entry.label}
    </button>
  );
}
