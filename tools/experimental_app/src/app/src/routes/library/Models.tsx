import * as React from "react";

import { ConfirmDeleteDialog } from "@/components/ConfirmDeleteDialog";
import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import { Button } from "@/components/ui/button";
import { useAddModel, useModels, useRemoveModel } from "@/hooks/queries";
import type { CatalogEntry } from "@/types";

// The Library Models catalog (ADR 0011 as amended by ticket 10, spec §A.6/§B.2): a
// user-editable list of OpenRouter slugs. A user adds a model once (slug + label) and it then
// appears as a labeled checkbox on every future Run launch — no more pasting the slug each
// time; entries can also be removed. Model *selection* still happens inline at Run launch (the
// LaunchRun chips with their free-text escape hatch); this screen owns catalog *editing*.

const FIELD_CLASS =
  "w-full rounded-md border bg-card px-2.5 py-2 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function Models() {
  const { data, isLoading, isError, error } = useModels();

  return (
    <section className="mx-auto max-w-[1280px]">
      <header className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight">Models</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          The catalog offered at run launch. Add a model once by its OpenRouter slug and a
          friendly label, and it becomes a labeled checkbox on every future run. A free-text
          slug can still be pasted inline at launch for one-offs.
        </p>
      </header>

      <AddModelForm />

      {isLoading ? (
        <LoadingBlock rows={4} />
      ) : isError || !data ? (
        <ErrorBlock error={error} />
      ) : data.catalog.length === 0 ? (
        <EmptyState
          title="No models in the catalog"
          description="Add a model above to offer it at run launch."
        />
      ) : (
        <div className="flex flex-col gap-2">
          {data.catalog.map((entry) => (
            <ModelRow key={entry.slug} entry={entry} />
          ))}
        </div>
      )}
    </section>
  );
}

/** Add a model to the catalog (slug + label). Re-adding a known slug re-labels it (upsert),
 * so this never errors on a duplicate; the server rejects a blank slug/label. On success the
 * fields clear and the catalog/launch options refetch (the hook invalidates them). */
function AddModelForm() {
  const [slug, setSlug] = React.useState("");
  const [label, setLabel] = React.useState("");
  const addModel = useAddModel();

  const canSubmit =
    slug.trim().length > 0 && label.trim().length > 0 && !addModel.isPending;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    addModel.mutate(
      { slug: slug.trim(), label: label.trim() },
      {
        onSuccess: () => {
          setSlug("");
          setLabel("");
        },
      },
    );
  }

  return (
    <form
      onSubmit={submit}
      className="mb-5 flex flex-col gap-3 rounded-lg border bg-card p-4"
    >
      <div className="text-sm font-semibold">Add a model</div>
      <div className="flex flex-col gap-3 sm:flex-row">
        <div className="flex-1">
          <label
            htmlFor="model-slug"
            className="mb-1.5 block text-xs font-semibold text-muted-foreground"
          >
            OpenRouter slug
          </label>
          <input
            id="model-slug"
            type="text"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="e.g. mistralai/pixtral-12b"
            className={`${FIELD_CLASS} font-mono text-[12px]`}
          />
        </div>
        <div className="flex-1">
          <label
            htmlFor="model-label"
            className="mb-1.5 block text-xs font-semibold text-muted-foreground"
          >
            Label
          </label>
          <input
            id="model-label"
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. Pixtral 12B"
            className={FIELD_CLASS}
          />
        </div>
      </div>

      {addModel.isError && <ErrorBlock error={addModel.error} />}

      <p className="text-[11.5px] text-muted-foreground">
        The slug isn’t validated here — a bad one simply fails at run time. Adding an existing
        slug updates its label.
      </p>
      <div className="flex justify-end">
        <Button type="submit" size="sm" disabled={!canSubmit}>
          {addModel.isPending ? "Adding…" : "Add model"}
        </Button>
      </div>
    </form>
  );
}

/** One catalog row: its label + slug, and a remove action. Removing an entry only drops it
 * from future launches — past Runs store the slug string, not a reference, so nothing else is
 * touched (ADR 0016). */
function ModelRow({ entry }: { entry: CatalogEntry }) {
  const removeModel = useRemoveModel();

  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border bg-card px-3.5 py-3">
      <span className="font-medium">{entry.label}</span>
      <div className="flex items-center gap-3">
        <span className="font-mono text-[12px] text-muted-foreground">
          {entry.slug}
        </span>
        <ConfirmDeleteDialog
          trigger={
            <Button variant="ghost" size="sm" className="text-destructive">
              Remove
            </Button>
          }
          title={`Remove “${entry.label}” from the catalog?`}
          description={
            <>
              This removes “{entry.label}” ({entry.slug}) from the run-launch options. Past
              runs that used it are unaffected — they store the slug, not a reference.
            </>
          }
          confirmLabel="Remove model"
          pending={removeModel.isPending}
          error={removeModel.error}
          onConfirm={() => removeModel.mutateAsync(entry.slug)}
        />
      </div>
    </div>
  );
}
