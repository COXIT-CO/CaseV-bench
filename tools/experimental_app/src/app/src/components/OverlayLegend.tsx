import { LABEL_COLORS } from "@/lib/labelColors";

/** The overlay's per-label colour key (ADR 0021, ticket 06): each ObjectType's fixed colour
 * next to its name, so the class-coloured boxes on an overlay are self-explanatory. Shared by
 * the Result prediction overlay and the Library ground-truth overlay — the two draw the same
 * palette, sourced from `LABEL_COLORS`, which mirrors the backend renderer. */
export function OverlayLegend() {
  return (
    <ul
      aria-label="Overlay colour legend"
      className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5 text-xs text-muted-foreground"
    >
      {LABEL_COLORS.map(({ label, color }) => (
        <li key={label} className="flex items-center gap-1.5">
          <span
            className="h-2.5 w-2.5 rounded-sm"
            style={{ backgroundColor: color }}
          />
          {label}
        </li>
      ))}
    </ul>
  );
}
