import type { RunKnobs } from "@/types";

// The read-only per-run knobs snapshot a Run recorded (spec: Runs 18; tickets 04/05). Shown on
// both the Run detail and the Result drill-down so a developer sees exactly how a result was
// produced and can reproduce it. A null temperature means the provider default; a null
// downsample_px means full resolution (no downsample) (ADR 0018/0019).

const KNOB_LABELS: { key: keyof RunKnobs; label: string; nullLabel: string }[] = [
  { key: "dpi", label: "DPI", nullLabel: "" },
  { key: "downsample_px", label: "Downsample px", nullLabel: "full resolution" },
  { key: "max_tokens", label: "max_tokens", nullLabel: "" },
  { key: "temperature", label: "temperature", nullLabel: "provider default" },
];

/** Render a snapshotted knob value; null renders the knob's own "off" label (a null
 * temperature is the provider default, a null downsample is full resolution). */
function knobValue(value: RunKnobs[keyof RunKnobs], nullLabel: string): string {
  return value === null ? nullLabel : String(value);
}

/** The per-run knobs snapshot as a labeled card grid. */
export function KnobsSnapshot({ knobs }: { knobs: RunKnobs }) {
  return (
    <div className="mb-6 rounded-lg border bg-card p-4">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        Per-run knobs snapshot
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {KNOB_LABELS.map(({ key, label, nullLabel }) => (
          <div key={key}>
            <div className="mb-0.5 text-[11px] text-muted-foreground">
              {label}
            </div>
            <div className="font-mono text-[13px]">
              {knobValue(knobs[key], nullLabel)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
