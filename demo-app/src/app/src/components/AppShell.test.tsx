import { screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/AppShell";
import { renderWithProviders } from "@/test/render";
import { META } from "@/test/fixtures";

// Mock the API module so the shell's React Query call resolves to a known payload.
vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return { ...actual, api: { meta: vi.fn() } };
});
import { api } from "@/api";

function renderShell() {
  return renderWithProviders(
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<div>home content</div>} />
      </Route>
    </Routes>,
  );
}

describe("AppShell", () => {
  beforeEach(() => {
    vi.mocked(api.meta).mockReset();
  });

  it("renders the primary nav, Library menu, and theme toggle", () => {
    vi.mocked(api.meta).mockResolvedValue(META);
    renderShell();

    // Primary nav (ADR 0011).
    expect(screen.getByRole("link", { name: "Leaderboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Runs" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Prompts" })).toBeInTheDocument();
    // Secondary Library menu + theme toggle.
    expect(screen.getByRole("button", { name: "Library" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /switch to (dark|light) theme/i }),
    ).toBeInTheDocument();
  });

  it("fetches /api/meta and renders the live counts", async () => {
    vi.mocked(api.meta).mockResolvedValue(META);
    renderShell();

    await waitFor(() =>
      expect(screen.getByText(/3 drawings/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/7 runs/)).toBeInTheDocument();
    expect(screen.getByText(/12 results/)).toBeInTheDocument();
  });

  it("surfaces an API error through the shared error block", async () => {
    vi.mocked(api.meta).mockRejectedValue(new Error("Could not reach the API."));
    renderShell();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the API."),
    );
  });
});
