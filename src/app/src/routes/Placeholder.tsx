import { EmptyState } from "@/components/states";

// Slice-0 route stubs. The AppShell + pipeline are what ticket 01 delivers; each feature
// screen replaces its stub in its own slice (Leaderboard/Result → slice 1, Runs → 2,
// Prompts → 3, Library → 4). Kept trivial so the nav is fully walkable today.
export function Placeholder({ title, slice }: { title: string; slice: string }) {
  return (
    <section className="mx-auto max-w-4xl">
      <h1 className="mb-4 text-lg font-semibold">{title}</h1>
      <EmptyState
        title={`${title} — coming in ${slice}`}
        description="The app shell, design tokens, and JSON pipeline are in place (ticket 01). This screen is built in a later slice."
      />
    </section>
  );
}
