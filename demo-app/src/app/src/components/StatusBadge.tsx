import { cn } from "@/lib/utils";
import type { RunStatus } from "@/types";

// One badge keyed by the semantic status palette (spec §C.2, ADR 0012): color encodes
// meaning, not decoration. Run status → queued (neutral) · running (warning) · done
// (success) · failed (danger). Each tone pairs a `-subtle` fill with its solid text.

const STATUS_CLASSES: Record<RunStatus, string> = {
  queued: "bg-neutral-subtle text-muted-foreground",
  running: "bg-warning-subtle text-warning",
  done: "bg-success-subtle text-success",
  failed: "bg-danger-subtle text-danger",
};

export function StatusBadge({
  status,
  className,
}: {
  status: RunStatus;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-block rounded px-2 py-0.5 text-[11px] font-semibold",
        STATUS_CLASSES[status],
        className,
      )}
    >
      {status}
    </span>
  );
}
