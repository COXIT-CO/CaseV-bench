import type { RunKnobs } from "@/types";

// The read-only per-run knobs snapshot a Run recorded (spec: Runs 18; ticket 04). Shown on
// both the Run detail and the Result drill-down so a developer sees exactly how a result was
// produced and can reproduce it. A null temperature means the provider default (ADR 0018/0019).

const KNOB_LABELS: { key: keyof RunKnobs; label: string }[] = [
  { key: "dpi", label: "DPI" },
  { key: "downsample_px", label: "Downsample px" },
  { key: "max_tokens", label: "max_tokens" },
  { key: "temperature", label: "temperature" },
];

/** Render a snapshotted knob value; a null temperature means the provider default. */
function knobValue(value: RunKnobs[keyof RunKnobs]): string {
  return value === null ? "provider default" : String(value);
}

/** The per-run knobs snapshot as a labeled card grid. */
export function KnobsSnapshot({ knobs }: { knobs: RunKnobs }) {
  return (
    <div className="mb-6 rounded-lg border bg-card p-4">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        Per-run knobs snapshot
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {KNOB_LABELS.map(({ key, label }) => (
          <div key={key}>
            <div className="mb-0.5 text-[11px] text-muted-foreground">
              {label}
            </div>
            <div className="font-mono text-[13px]">{knobValue(knobs[key])}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
