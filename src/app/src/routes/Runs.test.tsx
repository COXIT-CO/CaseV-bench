import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Runs } from "@/routes/Runs";
import { renderWithProviders } from "@/test/render";
import {
  EMPTY_LAUNCH_OPTIONS,
  LAUNCH_OPTIONS,
  RUN_HISTORY,
} from "@/test/fixtures";

// Mock the API module so the history + launch options resolve to known payloads.
vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: {
      runs: vi.fn(),
      launchOptions: vi.fn(),
      createRun: vi.fn(),
    },
  };
});
import { api } from "@/api";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="url">{loc.pathname + loc.search}</div>;
}

function renderRuns(route = "/runs") {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/runs" element={<Runs />} />
        <Route path="/runs/:id" element={<div>run detail page</div>} />
        <Route path="/prompts" element={<div>prompts page</div>} />
      </Routes>
      <LocationProbe />
    </>,
    { route },
  );
}

describe("Runs history", () => {
  beforeEach(() => {
    vi.mocked(api.runs).mockReset();
    vi.mocked(api.launchOptions).mockReset();
    vi.mocked(api.createRun).mockReset();
    vi.mocked(api.launchOptions).mockResolvedValue(LAUNCH_OPTIONS);
  });

  it("lists launched runs with status and a progress counter", async () => {
    vi.mocked(api.runs).mockResolvedValue(RUN_HISTORY);
    renderRuns();

    expect(await screen.findByText("#812")).toBeInTheDocument();
    // The running row shows its status badge and its progress / total.
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("3 / 6")).toBeInTheDocument();
    expect(screen.getByText("cabinet-count-v2")).toBeInTheDocument();
  });

  it("opens the run detail when a row is clicked", async () => {
    vi.mocked(api.runs).mockResolvedValue(RUN_HISTORY);
    renderRuns();

    await userEvent.click(await screen.findByRole("button", { name: "Open run 812" }));
    expect(await screen.findByText("run detail page")).toBeInTheDocument();
  });

  it("shows the empty state when there are no runs", async () => {
    vi.mocked(api.runs).mockResolvedValue({ runs: [] });
    renderRuns();

    expect(await screen.findByText("No runs yet")).toBeInTheDocument();
  });

  it("launches a run from the dialog and routes to its detail page", async () => {
    vi.mocked(api.runs).mockResolvedValue({ runs: [] });
    vi.mocked(api.createRun).mockResolvedValue({
      id: 900,
      status: "queued",
      task: "counting",
      total_units: 4,
    });
    renderRuns();

    // Open the launch dialog (the header CTA), pick a model, and submit — scoped to the
    // dialog so its "Launch run" submit isn't confused with the page's trigger buttons.
    await userEvent.click(
      (await screen.findAllByRole("button", { name: "Launch run" }))[0],
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Claude Sonnet 4.5" }),
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Launch run" }),
    );

    // React Query passes (variables, context) to the mutationFn; only the body matters.
    await waitFor(() =>
      expect(api.createRun).toHaveBeenCalledWith(
        expect.objectContaining({
          prompt_id: 9,
          drawing_id: 3,
          models: ["anthropic/claude-sonnet-4.5"],
        }),
        expect.anything(),
      ),
    );
    expect(await screen.findByText("run detail page")).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent("/runs/900");
  });

  it("guides to create a prompt/drawing when the launch options are empty", async () => {
    vi.mocked(api.runs).mockResolvedValue({ runs: [] });
    vi.mocked(api.launchOptions).mockResolvedValue(EMPTY_LAUNCH_OPTIONS);
    renderRuns();

    await userEvent.click(
      (await screen.findAllByRole("button", { name: "Launch run" }))[0],
    );
    expect(await screen.findByText("Nothing to run yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create a prompt" })).toBeInTheDocument();
  });
});
