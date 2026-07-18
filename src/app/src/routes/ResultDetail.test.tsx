import { screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ResultDetail } from "@/routes/ResultDetail";
import { renderWithProviders } from "@/test/render";
import {
  COUNTING_RESULT,
  LOCATION_RESULT,
  UNSCORED_LOCATION_RESULT,
  UNSCORED_RESULT,
} from "@/test/fixtures";

// Mock the API so the drill-down resolves to a known payload per Result.
vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return { ...actual, api: { result: vi.fn() } };
});
import { api } from "@/api";

function renderDetail(route = "/results/42") {
  return renderWithProviders(
    <Routes>
      <Route path="/results/:id" element={<ResultDetail />} />
      <Route path="/runs/:id" element={<div>run page</div>} />
      <Route path="/library/drawings/:id" element={<div>drawing gt page</div>} />
    </Routes>,
    { route },
  );
}

describe("ResultDetail", () => {
  beforeEach(() => {
    vi.mocked(api.result).mockReset();
  });

  it("renders the counting score headline, per-label rows, and header refs", async () => {
    vi.mocked(api.result).mockResolvedValue(COUNTING_RESULT);
    renderDetail();

    await screen.findByRole("heading", { name: "Result #42" });
    // Counting headline: total abs. error + exact matches as n / total.
    expect(screen.getByText("Total abs. error")).toBeInTheDocument();
    expect(screen.getByText("3 / 4")).toBeInTheDocument();
    // A per-label row shows predicted / gt / abs-error.
    const row = screen.getByText("cabinets").closest("tr")!;
    expect(within(row).getByText("24")).toBeInTheDocument();
    expect(within(row).getByText("22")).toBeInTheDocument();
    // Header refs link to the Run and the Drawing.
    expect(screen.getByRole("link", { name: /Run #812/ })).toHaveAttribute(
      "href",
      "/runs/812",
    );
    expect(screen.getByRole("link", { name: /Drawing: prj0001/ })).toBeInTheDocument();
  });

  it("shows a failed page's parse error and a successful page's parsed JSON", async () => {
    vi.mocked(api.result).mockResolvedValue(COUNTING_RESULT);
    renderDetail();

    await screen.findByText("Per-page predictions");
    // The ok page's parsed JSON is pretty-printed in a mono block.
    expect(screen.getByText(/"cabinets": 24/)).toBeInTheDocument();
    // The failed page surfaces its parse error distinctly.
    expect(screen.getByText("response was not valid JSON")).toBeInTheDocument();
    // Every page also exposes its raw model output (the failed page's raw text here).
    expect(screen.getAllByText("Raw model output").length).toBeGreaterThan(0);
    expect(screen.getByText("not json")).toBeInTheDocument();
  });

  it("renders the location score shape with P/R/F1 and per-label tp/fp/fn", async () => {
    vi.mocked(api.result).mockResolvedValue(LOCATION_RESULT);
    renderDetail("/results/90");

    await screen.findByRole("heading", { name: "Result #90" });
    expect(screen.getByText("Precision")).toBeInTheDocument();
    expect(screen.getByText("Recall")).toBeInTheDocument();
    expect(
      screen.getByText(/IoU@0.5, matched per page then micro-averaged/),
    ).toBeInTheDocument();
    // The counting columns are never shown for a location Result.
    expect(screen.queryByText("Total abs. error")).not.toBeInTheDocument();
    const row = screen.getByText("cabinets").closest("tr")!;
    expect(within(row).getByText("22")).toBeInTheDocument(); // tp
  });

  it("shows the prediction-only overlay grid for a scored location Result", async () => {
    vi.mocked(api.result).mockResolvedValue(LOCATION_RESULT);
    renderDetail("/results/90");

    // The GT visuals are gone (ticket 01): a scored location Result shows the model's own
    // prediction overlays, not the red-over-green compare grid.
    await screen.findByText("Predicted boxes");
    expect(screen.queryByText("Ground truth vs. prediction")).not.toBeInTheDocument();
    expect(screen.queryByText("no ground truth")).not.toBeInTheDocument();

    // Each overlay links the prediction-overlay PNG under /api, never the compare route.
    const links = screen.getAllByRole("link");
    const overlayLink = links.find((a) =>
      a.getAttribute("href")?.includes("/api/results/90/pages/1/overlay"),
    );
    expect(overlayLink).toBeDefined();
    expect(
      links.some((a) => a.getAttribute("href")?.includes("compare-overlay")),
    ).toBe(false);
  });

  it("renders the unscored state with a ground-truth CTA and no score block", async () => {
    vi.mocked(api.result).mockResolvedValue(UNSCORED_RESULT);
    renderDetail("/results/43");

    await screen.findByText("No ground truth for this drawing yet");
    // The CTA points at the Drawing's ground-truth entry.
    expect(screen.getByRole("link", { name: "Add ground truth" })).toHaveAttribute(
      "href",
      "/library/drawings/3#ground-truth",
    );
    // No score headline is shown, but the predictions are still inspectable.
    expect(screen.queryByText("Total abs. error")).not.toBeInTheDocument();
    expect(screen.getByText("Per-page predictions")).toBeInTheDocument();
  });

  it("shows prediction-only overlays for an unscored location Result", async () => {
    vi.mocked(api.result).mockResolvedValue(UNSCORED_LOCATION_RESULT);
    renderDetail("/results/44");

    // The unscored CTA is still shown, but so are the model's own predicted boxes — via the
    // prediction-overlay route, not the compare route (there is no ground truth to compare).
    await screen.findByText("No ground truth for this drawing yet");
    expect(screen.getByText("Predicted boxes")).toBeInTheDocument();
    expect(screen.queryByText("Ground truth vs. prediction")).not.toBeInTheDocument();

    const link = screen
      .getAllByRole("link")
      .find((a) =>
        a.getAttribute("href")?.includes("/api/results/44/pages/1/overlay"),
      );
    expect(link).toBeDefined();
    // …and it points at the prediction overlay, never the compare overlay.
    expect(link?.getAttribute("href")).not.toContain("compare-overlay");
  });

  it("surfaces an API error through the shared error block", async () => {
    vi.mocked(api.result).mockRejectedValue(new Error("boom"));
    renderDetail();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("boom"),
    );
  });
});
