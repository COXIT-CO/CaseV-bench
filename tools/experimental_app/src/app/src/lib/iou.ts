// The read-time IoU knob, shared by the Leaderboard and the Result drill-down so the two
// agree on what a valid threshold is and offer the same choices.
//
// Scoring is a recompute on read (ADR 0004), so the operating point is a *viewing* choice,
// not a property of a Run. What it is emphatically not is a setting: the canonical point is
// server-side (`LOCATION_IOU_THRESHOLD`) and every response echoes it, so nothing here ever
// defines or persists an operating point — `null` means "whatever the server calls
// canonical", never a hardcoded 0.5.

/** The thresholds the dropdowns offer. A fixed short list rather than a free numeric input:
 * this is a "what does it look like over there" control, not a place to define a metric. */
export const IOU_CHOICES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9];

/** A `?iou_threshold=` worth sending, or `null` for the canonical view. The server validates
 * too (422 outside `(0, 1]`); this only keeps a junk URL from round-tripping. */
export function parseIouThreshold(raw: string | null): number | null {
  if (raw === null || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 && value <= 1 ? value : null;
}
