import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RunDetail } from "@/routes/RunDetail";
import { renderWithProviders } from "@/test/render";
import { RUN_DETAIL, RUN_STATUS_DONE } from "@/test/fixtures";
import type { RunStatusResponse } from "@/types";

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: { run: vi.fn(), runStatus: vi.fn(), deleteRun: vi.fn() },
  };
});
import { api } from "@/api";

function renderDetail(route = "/runs/812") {
  return renderWithProviders(
    <Routes>
      <Route path="/runs/:id" element={<RunDetail />} />
      <Route path="/runs" element={<div>run history page</div>} />
      <Route path="/results/:id" element={<div>result detail page</div>} />
    </Routes>,
    { route },
  );
}

const RUNNING_STATUS: RunStatusResponse = {
  status: "running",
  progress: 3,
  total_units: 6,
  results: RUN_DETAIL.results,
};

describe("RunDetail", () => {
  beforeEach(() => {
    vi.mocked(api.run).mockReset();
    vi.mocked(api.runStatus).mockReset();
    vi.mocked(api.deleteRun).mockReset();
    vi.mocked(api.run).mockResolvedValue(RUN_DETAIL);
  });

  it("renders the header, the fixed-knobs snapshot, and per-model rows", async () => {
    vi.mocked(api.runStatus).mockResolvedValue(RUNNING_STATUS);
    renderDetail();

    expect(await screen.findByText("Run #812")).toBeInTheDocument();
    // Fixed-knobs snapshot values are shown as read-only metadata.
    expect(screen.getByText("Fixed knobs snapshot")).toBeInTheDocument();
    expect(screen.getByText("4096")).toBeInTheDocument();
    expect(screen.getByText("anthropic/claude-sonnet-4.5")).toBeInTheDocument();
  });

  it("shows live status while running and no result links yet", async () => {
    vi.mocked(api.runStatus).mockResolvedValue(RUNNING_STATUS);
    renderDetail();

    await screen.findByText("Run #812");
    expect(screen.getByText("running")).toBeInTheDocument();
    // Non-terminal: results are not yet viewable.
    expect(screen.getAllByText("running…").length).toBe(2);
    expect(
      screen.queryByRole("link", { name: /view result/i }),
    ).not.toBeInTheDocument();
    // A running Run can't be deleted (its runner is still writing), so no delete affordance.
    expect(
      screen.queryByRole("button", { name: "Delete run" }),
    ).not.toBeInTheDocument();
  });

  it("surfaces result links once the run is terminal", async () => {
    vi.mocked(api.runStatus).mockResolvedValue(RUN_STATUS_DONE);
    renderDetail();

    await screen.findByText("Run #812");
    await waitFor(() =>
      expect(screen.getByText("done")).toBeInTheDocument(),
    );
    const links = screen.getAllByRole("link", { name: /view result/i });
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("href", "/results/42");
  });

  it("treats a non-numeric id as not found", async () => {
    renderDetail("/runs/not-a-number");
    expect(await screen.findByText("Run not found")).toBeInTheDocument();
  });

  it("deletes the run after a confirmation stating the collateral", async () => {
    const user = userEvent.setup();
    vi.mocked(api.runStatus).mockResolvedValue(RUN_STATUS_DONE);
    vi.mocked(api.deleteRun).mockResolvedValue({ runs: 1, results: 2 });
    renderDetail();

    await screen.findByText("Run #812");
    // The delete affordance appears only once the run is terminal.
    await user.click(await screen.findByRole("button", { name: "Delete run" }));

    // The confirmation states the collateral (2 results) and the irreversible warning.
    expect(
      await screen.findByText(/permanently deletes run #812 and its 2 results/i),
    ).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();

    // Confirm — the dialog's own destructive button, not the header trigger.
    const confirm = screen
      .getAllByRole("button", { name: "Delete run" })
      .at(-1)!;
    await user.click(confirm);

    await waitFor(() => expect(api.deleteRun).toHaveBeenCalledWith(812));
    // On success we route back to the run history.
    expect(await screen.findByText("run history page")).toBeInTheDocument();
  });
});
