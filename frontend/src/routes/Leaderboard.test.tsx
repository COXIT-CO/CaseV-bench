import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Leaderboard } from "@/routes/Leaderboard";
import { renderWithProviders } from "@/test/render";
import { COUNTING_BOARD, EMPTY_BOARD, LOCATION_BOARD } from "@/test/fixtures";

// Mock the API module so the board resolves to a known payload per Task.
vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return { ...actual, api: { leaderboard: vi.fn() } };
});
import { api } from "@/api";
import type { Task } from "@/types";

// Surfaces the live URL so tests can assert the board mirrors its filters (spec §B.3).
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="url">{loc.pathname + loc.search}</div>;
}

function renderBoard(route = "/") {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/" element={<Leaderboard />} />
        <Route path="/results/:id" element={<div>result detail page</div>} />
        <Route
          path="/library/drawings/:id"
          element={<div>drawing gt page</div>}
        />
      </Routes>
      <LocationProbe />
    </>,
    { route },
  );
}

/** Default: counting board unless the query asks for location. */
function boardByTask() {
  vi.mocked(api.leaderboard).mockImplementation(({ task }: { task: Task }) =>
    Promise.resolve(task === "location" ? LOCATION_BOARD : COUNTING_BOARD),
  );
}

describe("Leaderboard", () => {
  beforeEach(() => {
    vi.mocked(api.leaderboard).mockReset();
  });

  it("renders counting columns, a 1-based rank, and the exact-match denominator", async () => {
    boardByTask();
    renderBoard();

    await waitFor(() =>
      expect(
        screen.getByRole("columnheader", { name: "Total abs. error" }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("columnheader", { name: "Exact matches" }),
    ).toBeInTheDocument();
    // The leader shows rank #1 and its exact matches as `n / total`.
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("4 / 4")).toBeInTheDocument();
    expect(screen.getByText("anthropic/claude-sonnet-4.5")).toBeInTheDocument();
  });

  it("shows unscored rows unranked with a ground-truth CTA to the drawing", async () => {
    boardByTask();
    renderBoard();

    const gtLink = await screen.findByRole("link", { name: /ground truth/i });
    // The CTA points at the unscored row's own Drawing (id 5 in the fixture).
    expect(gtLink).toHaveAttribute("href", "/library/drawings/5");
  });

  it("swaps metrics and columns when the Task tab changes, mirroring it to the URL", async () => {
    boardByTask();
    renderBoard();

    await screen.findByRole("columnheader", { name: "Total abs. error" });
    await userEvent.click(screen.getByRole("tab", { name: "Location" }));

    await waitFor(() =>
      expect(
        screen.getByRole("columnheader", { name: "F1" }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("columnheader", { name: "Precision" }),
    ).toBeInTheDocument();
    // Counting-only columns are gone, and the Task is in the URL.
    expect(
      screen.queryByRole("columnheader", { name: "Total abs. error" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent("task=location");
    expect(api.leaderboard).toHaveBeenCalledWith(
      expect.objectContaining({ task: "location" }),
    );
  });

  it("reflects the Drawing filter into the URL", async () => {
    boardByTask();
    renderBoard();

    await screen.findByRole("columnheader", { name: "Total abs. error" });
    await userEvent.selectOptions(
      screen.getByLabelText("Drawing"),
      "prj0002",
    );

    expect(screen.getByTestId("url")).toHaveTextContent("drawing_id=5");
    await waitFor(() =>
      expect(api.leaderboard).toHaveBeenCalledWith(
        expect.objectContaining({ drawing_id: 5 }),
      ),
    );
  });

  it("loads a URL with filters and reproduces that exact board", async () => {
    boardByTask();
    renderBoard("/?task=location&drawing_id=3&sort=precision");

    await screen.findByRole("columnheader", { name: "F1" });
    expect(api.leaderboard).toHaveBeenCalledWith({
      task: "location",
      drawing_id: 3,
      sort: "precision",
    });
  });

  it("navigates to the Result detail route when a row is clicked", async () => {
    boardByTask();
    renderBoard();

    const row = await screen.findByRole("button", {
      name: /open result for anthropic\/claude-sonnet-4.5/i,
    });
    await userEvent.click(row);

    expect(await screen.findByText("result detail page")).toBeInTheDocument();
  });

  it("the ground-truth CTA navigates to GT entry, not the Result detail", async () => {
    boardByTask();
    renderBoard();

    await userEvent.click(await screen.findByRole("link", { name: /ground truth/i }));

    expect(await screen.findByText("drawing gt page")).toBeInTheDocument();
    expect(screen.queryByText("result detail page")).not.toBeInTheDocument();
  });

  it("shows the empty state when there are no results", async () => {
    vi.mocked(api.leaderboard).mockResolvedValue(EMPTY_BOARD);
    renderBoard();

    expect(
      await screen.findByText("No results yet — launch a run"),
    ).toBeInTheDocument();
  });

  it("surfaces an API error through the shared error block", async () => {
    vi.mocked(api.leaderboard).mockRejectedValue(new Error("boom"));
    renderBoard();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("boom"),
    );
  });
});
