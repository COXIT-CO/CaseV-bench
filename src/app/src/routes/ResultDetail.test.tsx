import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ResultDetailResponse } from "@/types";
import { ResultDetail } from "@/routes/ResultDetail";
import { renderWithProviders } from "@/test/render";
import {
  EDITED_LOCATION_RESULT,
  FAILED_PAGE_LOCATION_RESULT,
  LOCATION_RESULT,
  SALVAGED_LOCATION_RESULT,
  UNSCORED_LOCATION_RESULT,
} from "@/test/fixtures";

// Mock the API so the drill-down resolves to a known payload per Result.
vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: {
      result: vi.fn(),
      setPredictionOverride: vi.fn(),
      revertPredictionOverride: vi.fn(),
    },
  };
});
import { ApiError, api } from "@/api";

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
    vi.mocked(api.setPredictionOverride).mockReset();
    vi.mocked(api.revertPredictionOverride).mockReset();
  });

  it("shows a failed page's parse error and a successful page's parsed JSON", async () => {
    vi.mocked(api.result).mockResolvedValue(FAILED_PAGE_LOCATION_RESULT);
    renderDetail("/results/45");

    await screen.findByText("Per-page predictions");
    // The ok page's parsed JSON is pretty-printed in a mono block.
    expect(screen.getByText(/"label": "cabinet"/)).toBeInTheDocument();
    // The failed page surfaces its parse error distinctly.
    expect(screen.getByText("response was not valid JSON")).toBeInTheDocument();
    // Every page also exposes its raw model output (the failed page's raw text here).
    expect(screen.getAllByText("Raw model output").length).toBeGreaterThan(0);
    expect(screen.getByText("not json")).toBeInTheDocument();
  });

  it("renders the score shape with P/R/F1, per-label tp/fp/fn, and header refs", async () => {
    vi.mocked(api.result).mockResolvedValue(LOCATION_RESULT);
    renderDetail("/results/90");

    await screen.findByRole("heading", { name: "Result #90" });
    expect(screen.getByText("Precision")).toBeInTheDocument();
    expect(screen.getByText("Recall")).toBeInTheDocument();
    expect(
      screen.getByText(/IoU@0.50, matched per page then micro-averaged/),
    ).toBeInTheDocument();
    // One Score shape: the counting block is gone, not branched around (ADR 0032).
    expect(screen.queryByText("Total abs. error")).not.toBeInTheDocument();
    expect(screen.queryByText("Exact matches")).not.toBeInTheDocument();
    // "cabinet" now also appears in the overlay legend (ticket 06); scope to the score row.
    const row = screen
      .getAllByText("cabinet")
      .map((el) => el.closest("tr"))
      .find((tr): tr is HTMLTableRowElement => tr !== null)!;
    expect(within(row).getByText("22")).toBeInTheDocument(); // tp
    // Header refs link to the Run and the Drawing.
    expect(screen.getByRole("link", { name: /Run #12/ })).toHaveAttribute(
      "href",
      "/runs/12",
    );
    expect(
      screen.getByRole("link", { name: /Drawing: floorplan/ }),
    ).toBeInTheDocument();
  });

  it("shows the prediction-only overlay grid for a scored location Result", async () => {
    vi.mocked(api.result).mockResolvedValue(LOCATION_RESULT);
    renderDetail("/results/90");

    // The GT visuals are gone (ticket 01): a scored location Result shows the model's own
    // prediction overlays, not the red-over-green compare grid.
    await screen.findByText("Predicted boxes");
    expect(screen.queryByText("Ground truth vs. prediction")).not.toBeInTheDocument();
    expect(screen.queryByText("no ground truth")).not.toBeInTheDocument();

    // The legend is a per-label colour key (ticket 06), not a single "Prediction" swatch:
    // every ObjectType is listed, next to its own colour.
    expect(screen.queryByText("Prediction")).not.toBeInTheDocument();
    const legend = screen.getByRole("list", { name: "Overlay colour legend" });
    for (const label of [
      "cabinet",
      "countertop",
      "elevation",
      "elevation_callout",
    ]) {
      expect(within(legend).getByText(label)).toBeInTheDocument();
    }

    // Each overlay shows the prediction-overlay PNG under /api, never the compare route.
    const overlay = screen.getByAltText("predicted boxes for page 1");
    expect(overlay).toHaveAttribute(
      "src",
      "/api/results/90/pages/1/overlay",
    );
    // The overlays open the in-app lightbox — they are no longer anchors that navigate away
    // (ticket 08), and never point at the removed compare route.
    expect(overlay.closest("a")).toBeNull();
    expect(
      screen
        .queryAllByRole("link")
        .some((a) => a.getAttribute("href")?.includes("compare-overlay")),
    ).toBe(false);
  });

  it("renders the unscored state with a ground-truth CTA and no score block", async () => {
    vi.mocked(api.result).mockResolvedValue(UNSCORED_LOCATION_RESULT);
    renderDetail("/results/44");

    await screen.findByText("No ground truth for this drawing yet");
    // The CTA points at the Drawing's ground-truth entry.
    expect(screen.getByRole("link", { name: "Add ground truth" })).toHaveAttribute(
      "href",
      "/library/drawings/3#ground-truth",
    );
    // No score headline is shown, but the predictions are still inspectable.
    expect(screen.queryByText("Precision")).not.toBeInTheDocument();
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

    const overlay = screen.getByAltText("predicted boxes for page 1");
    expect(overlay).toHaveAttribute(
      "src",
      "/api/results/44/pages/1/overlay",
    );
    // …the prediction overlay, opened in-app (not an anchor) and never the compare overlay.
    expect(overlay.closest("a")).toBeNull();
  });

  it("renders a salvaged error page's boxes, JSON, and overlay — not a bare failure", async () => {
    vi.mocked(api.result).mockResolvedValue(SALVAGED_LOCATION_RESULT);
    renderDetail("/results/91");

    await screen.findByText("Predicted boxes");
    // The salvaged page keeps its error note but also shows the recovered JSON…
    expect(
      screen.getByText(/response was truncated; salvaged intact array elements/),
    ).toBeInTheDocument();
    expect(screen.getByText("Salvaged output")).toBeInTheDocument();
    // …and it is marked "salvaged" rather than a bare "prediction failed" placeholder.
    expect(screen.getByText("salvaged")).toBeInTheDocument();
    expect(screen.queryByText("prediction failed")).not.toBeInTheDocument();

    // The salvaged page still shows its rendered overlay (drawn from the boxes that parsed).
    expect(screen.getByAltText("predicted boxes for page 1")).toHaveAttribute(
      "src",
      "/api/results/91/pages/1/overlay",
    );
  });

  it("opens the overlay lightbox on a card click, pages the set, and closes on Esc", async () => {
    const user = userEvent.setup();
    vi.mocked(api.result).mockResolvedValue(LOCATION_RESULT);
    renderDetail("/results/90");

    await screen.findByText("Predicted boxes");
    // No dialog until a card is clicked, and the overlays are buttons, not new-tab anchors.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    // Clicking page 1's overlay opens the viewer at that image with a counter over the set.
    await user.click(screen.getByAltText("predicted boxes for page 1"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("1 / 2")).toBeInTheDocument();
    expect(within(dialog).getByRole("img")).toHaveAttribute(
      "src",
      "/api/results/90/pages/1/overlay",
    );

    // Next moves across the Result's overlays with the counter following.
    await user.click(within(dialog).getByRole("button", { name: /next image/i }));
    expect(within(dialog).getByText("2 / 2")).toBeInTheDocument();
    expect(within(dialog).getByRole("img")).toHaveAttribute(
      "src",
      "/api/results/90/pages/2/overlay",
    );

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("edits a location prediction's JSON, redraws the overlay, and shows the edited badge", async () => {
    const user = userEvent.setup();
    // The drill-down flips to the edited payload once the override is saved, so the
    // invalidation-driven refetch returns the edited boxes.
    let current: ResultDetailResponse = LOCATION_RESULT;
    vi.mocked(api.result).mockImplementation(() => Promise.resolve(current));
    vi.mocked(api.setPredictionOverride).mockImplementation(async () => {
      current = EDITED_LOCATION_RESULT;
      return EDITED_LOCATION_RESULT.predictions[0];
    });
    renderDetail("/results/90");

    await screen.findByText("Per-page predictions");
    // Before editing, page 1's overlay is served from the bare (cached) URL.
    expect(
      screen.getByAltText("predicted boxes for page 1").getAttribute("src"),
    ).toBe("/api/results/90/pages/1/overlay");

    // Open the editor on page 1, replace the JSON, and save.
    await user.click(screen.getAllByRole("button", { name: "Edit JSON" })[0]);
    const editor = screen.getByLabelText("Edit prediction JSON for page 1");
    fireEvent.change(editor, { target: { value: '{"detections": []}' } });
    await user.click(screen.getByRole("button", { name: /save & redraw/i }));

    // The override is sent for (result 90, page 1) with the edited text.
    await waitFor(() =>
      expect(api.setPredictionOverride).toHaveBeenCalledWith(
        90,
        1,
        '{"detections": []}',
      ),
    );
    // After the refetch the page is marked "edited" (on both the overlay card and JSON view)…
    await waitFor(() =>
      expect(screen.getAllByText("edited").length).toBeGreaterThan(0),
    );
    // …and the overlay is cache-busted so the corrected boxes actually redraw.
    expect(
      screen.getByAltText("predicted boxes for page 1").getAttribute("src"),
    ).toContain("?edited=");
  });

  it("keeps the editor open and surfaces the precise error on a rejected edit", async () => {
    const user = userEvent.setup();
    vi.mocked(api.result).mockResolvedValue(LOCATION_RESULT);
    vi.mocked(api.setPredictionOverride).mockRejectedValue(
      new ApiError(400, "detections.0.label: Input should be 'cabinet', ..."),
    );
    renderDetail("/results/90");

    await screen.findByText("Per-page predictions");
    await user.click(screen.getAllByRole("button", { name: "Edit JSON" })[0]);
    fireEvent.change(
      screen.getByLabelText("Edit prediction JSON for page 1"),
      { target: { value: '{"detections": [{"label": "walls"}]}' } },
    );
    await user.click(screen.getByRole("button", { name: /save & redraw/i }));

    // The precise validation error is shown and the editor stays open (nothing was saved).
    await screen.findByText(/detections\.0\.label/);
    expect(
      screen.getByLabelText("Edit prediction JSON for page 1"),
    ).toBeInTheDocument();
    // No "edited" badge — the rejected edit never took effect.
    expect(screen.queryByText("edited")).not.toBeInTheDocument();
  });

  it("reverts an edited location prediction back to the model output", async () => {
    const user = userEvent.setup();
    let current: ResultDetailResponse = EDITED_LOCATION_RESULT;
    vi.mocked(api.result).mockImplementation(() => Promise.resolve(current));
    vi.mocked(api.revertPredictionOverride).mockImplementation(async () => {
      current = LOCATION_RESULT;
      return LOCATION_RESULT.predictions[0];
    });
    renderDetail("/results/90");

    await screen.findByText("Per-page predictions");
    // The edited page shows the badge and a revert affordance.
    expect(screen.getAllByText("edited").length).toBeGreaterThan(0);
    await user.click(
      screen.getByRole("button", { name: /revert to model output/i }),
    );

    await waitFor(() =>
      expect(api.revertPredictionOverride).toHaveBeenCalledWith(90, 1),
    );
    // After the refetch the override is gone — no "edited" badge remains.
    await waitFor(() =>
      expect(screen.queryByText("edited")).not.toBeInTheDocument(),
    );
  });

  it("surfaces an API error through the shared error block", async () => {
    vi.mocked(api.result).mockRejectedValue(new Error("boom"));
    renderDetail();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("boom"),
    );
  });
});

describe("ResultDetail IoU knob", () => {
  // The exploratory twin of LOCATION_RESULT: same Result, re-scored at 0.3 by the server,
  // which is what makes the loose boxes count.
  const EXPLORED: ResultDetailResponse = {
    ...LOCATION_RESULT,
    iou_threshold: 0.3,
    canonical_iou: false,
    location_score: {
      ...LOCATION_RESULT.location_score!,
      precision: 0.94,
      recall: 0.91,
      f1: 0.92,
    },
  };

  // Surfaces the live URL so the knob can be asserted to mirror into it, the way the
  // Leaderboard's filters do (spec §B.3) — that mirroring is what makes an exploratory view
  // shareable and back/forward-able.
  function UrlProbe() {
    const loc = useLocation();
    return <div data-testid="url">{loc.pathname + loc.search}</div>;
  }

  function renderKnob(route: string) {
    return renderWithProviders(
      <>
        <Routes>
          <Route path="/results/:id" element={<ResultDetail />} />
        </Routes>
        <UrlProbe />
      </>,
      { route },
    );
  }

  beforeEach(() => {
    vi.mocked(api.result).mockReset();
  });

  it("requests the threshold from the URL and labels the rates with it", async () => {
    vi.mocked(api.result).mockResolvedValue(EXPLORED);
    renderDetail("/results/90?iou_threshold=0.3");

    await screen.findByRole("heading", { name: "Result #90" });
    expect(api.result).toHaveBeenCalledWith(90, 0.3);
    expect(
      screen.getByText(/IoU@0.30, matched per page then micro-averaged/),
    ).toBeInTheDocument();
    // The banner names what the exploration is deviating *from*, read off the response
    // rather than hardcoded, and says plainly that nothing was stored.
    expect(screen.getByRole("status")).toHaveTextContent("Exploring at IoU 0.30");
    expect(screen.getByRole("status")).toHaveTextContent("IoU 0.50");
  });

  it("defaults to the canonical point and shows no exploring banner", async () => {
    vi.mocked(api.result).mockResolvedValue(LOCATION_RESULT);
    renderDetail("/results/90");

    await screen.findByRole("heading", { name: "Result #90" });
    expect(api.result).toHaveBeenCalledWith(90, null);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("re-scores from the dropdown and mirrors the choice into the URL", async () => {
    vi.mocked(api.result).mockImplementation(async (_id, threshold) =>
      threshold === 0.3 ? EXPLORED : LOCATION_RESULT,
    );
    renderKnob("/results/90");
    await screen.findByRole("heading", { name: "Result #90" });

    await userEvent.selectOptions(screen.getByLabelText("IoU"), "0.3");

    await waitFor(() => expect(api.result).toHaveBeenCalledWith(90, 0.3));
    expect(screen.getByTestId("url")).toHaveTextContent(
      "/results/90?iou_threshold=0.3",
    );
    // The rendered rates really came back re-scored, not just the request re-issued: the
    // subtitle and banner both track the new operating point.
    await waitFor(() =>
      expect(
        screen.getByText(/IoU@0.30, matched per page then micro-averaged/),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Exploring at IoU 0.30");
  });

  it("returns to the canonical point from the banner", async () => {
    vi.mocked(api.result).mockImplementation(async (_id, threshold) =>
      threshold === 0.3 ? EXPLORED : LOCATION_RESULT,
    );
    renderKnob("/results/90?iou_threshold=0.3");
    await screen.findByRole("heading", { name: "Result #90" });

    await userEvent.click(screen.getByRole("button", { name: /back to default/i }));

    await waitFor(() => expect(api.result).toHaveBeenCalledWith(90, null));
    expect(screen.getByTestId("url")).toHaveTextContent("/results/90");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
