import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { Leaderboard } from "@/routes/Leaderboard";
import { NotFound } from "@/routes/NotFound";
import { Placeholder } from "@/routes/Placeholder";
import { Prompts } from "@/routes/Prompts";
import { Runs } from "@/routes/Runs";
import { Drawings } from "@/routes/library/Drawings";
import { Models } from "@/routes/library/Models";

// Client-side routes (spec §B.2). The AppShell is the layout route; feature screens
// render into its <Outlet/>. Detail routes (/results/:id, /runs/:id, /prompts/:task/:family,
// /library/drawings/:id) are wired in as their slices land.
export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Leaderboard />} />
        <Route
          path="results/:id"
          element={<Placeholder title="Result detail" slice="the next slice" />}
        />
        <Route path="runs" element={<Runs />} />
        <Route path="prompts" element={<Prompts />} />
        <Route path="library">
          <Route index element={<Navigate to="/library/drawings" replace />} />
          <Route path="drawings" element={<Drawings />} />
          <Route path="models" element={<Models />} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
