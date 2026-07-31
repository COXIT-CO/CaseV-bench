import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Leaderboard } from "@/routes/Leaderboard";
import { renderWithProviders } from "@/test/render";
import {
  EMPTY_BOARD,
  LOCATION_BOARD,
  PROMPTS,
  PROMPT_HISTORY,
} from "@/test/fixtures";

// Mock the API module so the board resolves to a known payload, and the prompt
// family/version dropdowns have families + a version history to populate from (ticket 04).
vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: {
      leaderboard: vi.fn(),
      prompts: vi.fn(),
      promptHistory: vi.fn(),
    },
  };
});
import { api } from "@/api";

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

function board() {
  vi.mocked(api.leaderboard).mockResolvedValue(LOCATION_BOARD);
  vi.mocked(api.prompts).mockResolvedValue(PROMPTS);
  vi.mocked(api.promptHistory).mockResolvedValue(PROMPT_HISTORY);
}

describe("Leaderboard", () => {
  beforeEach(() => {
    vi.mocked(api.leaderboard).mockReset();
    vi.mocked(api.prompts).mockReset();
    vi.mocked(api.promptHistory).mockReset();
    // The prompt dropdowns fetch families/versions on every render; give them a default
    // payload so tests that only exercise the board don't hit an undefined query fn.
    vi.mocked(api.prompts).mockResolvedValue(PROMPTS);
    vi.mocked(api.promptHistory).mockResolvedValue(PROMPT_HISTORY);
  });

  it("renders the score columns, a 1-based rank, and the ranked rates", async () => {
    board();
    renderBoard();

    await waitFor(() =>
      expect(
        screen.getByRole("columnheader", { name: "F1" }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("columnheader", { name: "Precision" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "Recall" }),
    ).toBeInTheDocument();
    // The leader shows rank #1 and its rates to two decimals.
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("0.73")).toBeInTheDocument();
    expect(screen.getByText("anthropic/claude-sonnet-4.5")).toBeInTheDocument();
  });

  it("offers no task choice and mirrors no task into the URL", async () => {
    board();
    renderBoard();

    await screen.findByRole("columnheader", { name: "F1" });
    // The task tablist is gone with the task itself (ADR 0032) — one benchmark, no choice.
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    expect(screen.getByTestId("url")).not.toHaveTextContent("task");
  });

  it("never sends a task with the board request", async () => {
    board();
    renderBoard("/?task=counting");

    await screen.findByRole("columnheader", { name: "F1" });
    // A stale ``?task=`` in the URL is not a filter the SPA knows about, so it is neither
    // parsed nor forwarded (ADR 0032).
    expect(api.leaderboard).toHaveBeenCalledWith(
      expect.not.objectContaining({ task: expect.anything() }),
    );
  });

  it("shows unscored rows unranked with a ground-truth CTA to the drawing", async () => {
    board();
    renderBoard();

    const gtLink = await screen.findByRole("link", { name: /ground truth/i });
    // The CTA points at the unscored row's own Drawing (id 5 in the fixture).
    expect(gtLink).toHaveAttribute("href", "/library/drawings/5#ground-truth");
  });

  it("reflects the Drawing filter into the URL", async () => {
    board();
    renderBoard();

    await screen.findByRole("columnheader", { name: "F1" });
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
    board();
    renderBoard("/?drawing_id=3&sort=precision");

    await screen.findByRole("columnheader", { name: "F1" });
    expect(api.leaderboard).toHaveBeenCalledWith({
      drawing_id: 3,
      prompt_family: null,
      prompt_version: null,
      sort: "precision",
    });
  });

  it("navigates to the Result detail route when a row is clicked", async () => {
    board();
    renderBoard();

    const row = await screen.findByRole("button", {
      name: /open result for anthropic\/claude-sonnet-4.5/i,
    });
    await userEvent.click(row);

    expect(await screen.findByText("result detail page")).toBeInTheDocument();
  });

  it("the ground-truth CTA navigates to GT entry, not the Result detail", async () => {
    board();
    renderBoard();

    await userEvent.click(await screen.findByRole("link", { name: /ground truth/i }));

    expect(await screen.findByText("drawing gt page")).toBeInTheDocument();
    expect(screen.queryByText("result detail page")).not.toBeInTheDocument();
  });

  it("filters by prompt family, mirroring it to the URL and querying with it", async () => {
    board();
    renderBoard();

    await screen.findByRole("columnheader", { name: "F1" });
    await userEvent.selectOptions(screen.getByLabelText("Prompt"), "boxes");

    expect(screen.getByTestId("url")).toHaveTextContent("prompt_family=boxes");
    await waitFor(() =>
      expect(api.leaderboard).toHaveBeenCalledWith(
        expect.objectContaining({ prompt_family: "boxes" }),
      ),
    );
  });

  it("keeps the version filter disabled until a family is chosen, then pins a version", async () => {
    board();
    renderBoard();

    await screen.findByRole("columnheader", { name: "F1" });
    // No family yet → the version dropdown is disabled.
    expect(screen.getByLabelText("Version")).toBeDisabled();

    await userEvent.selectOptions(screen.getByLabelText("Prompt"), "boxes");
    // Once the family's history loads, the version dropdown enables and offers its versions.
    await waitFor(() => expect(screen.getByLabelText("Version")).toBeEnabled());
    await userEvent.selectOptions(screen.getByLabelText("Version"), "2");

    expect(screen.getByTestId("url")).toHaveTextContent("prompt_version=2");
    await waitFor(() =>
      expect(api.leaderboard).toHaveBeenCalledWith(
        expect.objectContaining({
          prompt_family: "boxes",
          prompt_version: 2,
        }),
      ),
    );
  });

  it("loads a URL with a family + version and reproduces that exact query", async () => {
    board();
    renderBoard("/?prompt_family=boxes&prompt_version=2");

    await screen.findByRole("columnheader", { name: "F1" });
    expect(api.leaderboard).toHaveBeenCalledWith({
      drawing_id: null,
      prompt_family: "boxes",
      prompt_version: 2,
      sort: null,
    });
  });

  it("clearing the family clears the pinned version", async () => {
    board();
    renderBoard("/?prompt_family=boxes&prompt_version=2");

    await screen.findByRole("columnheader", { name: "F1" });
    await userEvent.selectOptions(screen.getByLabelText("Prompt"), "All prompts");

    expect(screen.getByTestId("url")).not.toHaveTextContent("prompt_family");
    expect(screen.getByTestId("url")).not.toHaveTextContent("prompt_version");
    await waitFor(() =>
      expect(api.leaderboard).toHaveBeenCalledWith(
        expect.objectContaining({ prompt_family: null, prompt_version: null }),
      ),
    );
  });

  it("offers only the location prompt families in the filter", async () => {
    board();
    renderBoard();

    await screen.findByRole("columnheader", { name: "F1" });
    const familyFilter = screen.getByLabelText("Prompt");
    expect(
      screen.getByRole("option", { name: "boxes" }),
    ).toBeInTheDocument();
    // Only the families the listing returned; nothing else reaches the dropdown.
    expect(familyFilter).not.toHaveTextContent("cabinet-count-v2");
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
