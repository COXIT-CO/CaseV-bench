import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import { useModels } from "@/hooks/queries";

// The Library Models catalog (ADR 0011, spec §A.6/§B.2): a read-only view of the curated
// OpenRouter slugs. Model *selection* still happens inline at Run launch (the
// ModelMultiSelect with its free-text escape hatch); this screen is catalog *viewing* only
// — the standalone selection-echo page from the flat nav is gone (ADR 0011).

export function Models() {
  const { data, isLoading, isError, error } = useModels();

  return (
    <section className="mx-auto max-w-[1280px]">
      <header className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight">Models</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          The curated catalog offered at run launch. Selection happens inline when you
          launch a run; a free-text slug can be pasted there too.
        </p>
      </header>

      {isLoading ? (
        <LoadingBlock rows={4} />
      ) : isError || !data ? (
        <ErrorBlock error={error} />
      ) : data.catalog.length === 0 ? (
        <EmptyState
          title="No models in the catalog"
          description="The curated catalog is empty."
        />
      ) : (
        <div className="flex flex-col gap-2">
          {data.catalog.map((entry) => (
            <div
              key={entry.slug}
              className="flex items-center justify-between gap-4 rounded-lg border bg-card px-3.5 py-3"
            >
              <span className="font-medium">{entry.label}</span>
              <span className="font-mono text-[12px] text-muted-foreground">
                {entry.slug}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
