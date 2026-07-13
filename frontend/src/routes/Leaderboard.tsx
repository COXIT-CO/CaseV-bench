import { Placeholder } from "@/routes/Placeholder";

// Landing page (ADR 0011). Full board (URL-shareable filters, unscored states, drill-down)
// arrives in slice 1.
export function Leaderboard() {
  return <Placeholder title="Leaderboard" slice="slice 1" />;
}
