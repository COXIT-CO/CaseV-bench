import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DrawingDetail } from "@/routes/library/DrawingDetail";
import { renderWithProviders } from "@/test/render";
import { COUNTING_GT, DRAWING_DETAIL } from "@/test/fixtures";

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: {
      drawing: vi.fn(),
      deleteDrawing: vi.fn(),
      // The detail now embeds the ground-truth entry, which pre-fills the counting totals.
      countingGroundTruth: vi.fn(),
      saveCountingGroundTruth: vi.fn(),
      importLocationGroundTruth: vi.fn(),
    },
  };
});
import { api } from "@/api";

function renderDetail(route = "/library/drawings/3") {
  return renderWithProviders(
    <Routes>
      <Route path="/library/drawings/:id" element={<DrawingDetail />} />
      <Route path="/library/drawings" element={<div>drawings list page</div>} />
    </Routes>,
    { route },
  );
}

describe("DrawingDetail", () => {
  beforeEach(() => {
    vi.mocked(api.drawing).mockReset();
    vi.mocked(api.deleteDrawing).mockReset();
    vi.mocked(api.countingGroundTruth).mockReset();
    vi.mocked(api.countingGroundTruth).mockResolvedValue(COUNTING_GT);
  });

  it("renders the page thumbnails with pixel dimensions and image URLs", async () => {
    vi.mocked(api.drawing).mockResolvedValue(DRAWING_DETAIL);
    renderDetail();

    expect(
      await screen.findByRole("heading", { name: "prj0001" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 pages")).toBeInTheDocument();
    expect(screen.getByText("1700×2200 px")).toBeInTheDocument();

    const image = screen.getByAltText("Page 1");
    expect(image).toHaveAttribute("src", "/api/drawings/3/pages/1/image");
    // The thumbnail opens the in-app lightbox — no longer an anchor that navigates away.
    expect(image.closest("a")).toBeNull();
  });

  it("opens the page lightbox on a thumbnail click, pages the set, and closes on Esc", async () => {
    const user = userEvent.setup();
    vi.mocked(api.drawing).mockResolvedValue(DRAWING_DETAIL);
    renderDetail();

    await screen.findByAltText("Page 1");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    // Clicking page 1 opens the viewer at that image, with a counter over the Drawing's pages.
    await user.click(screen.getByAltText("Page 1"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("1 / 2")).toBeInTheDocument();
    expect(within(dialog).getByRole("img")).toHaveAttribute(
      "src",
      "/api/drawings/3/pages/1/image",
    );

    // Next pages across the Drawing's images with the counter following.
    await user.click(within(dialog).getByRole("button", { name: /next image/i }));
    expect(within(dialog).getByText("2 / 2")).toBeInTheDocument();
    expect(within(dialog).getByRole("img")).toHaveAttribute(
      "src",
      "/api/drawings/3/pages/2/image",
    );

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows a GT overlay toggle that defaults off (plain page, no legend)", async () => {
    vi.mocked(api.drawing).mockResolvedValue(DRAWING_DETAIL);
    renderDetail();

    // The overlay toggle (ticket 06) reinstates the GT verification view; it is present but
    // off by default, so the page cards show the plain image and no colour legend yet.
    const toggle = await screen.findByRole("checkbox", {
      name: /ground-truth overlay/i,
    });
    expect(toggle).not.toBeChecked();
    expect(screen.getByAltText("Page 1")).toHaveAttribute(
      "src",
      "/api/drawings/3/pages/1/image",
    );
    expect(
      screen.queryByRole("list", { name: "Overlay colour legend" }),
    ).not.toBeInTheDocument();
  });

  it("toggles the GT overlay on: page images switch to the gt-overlay route and a legend appears", async () => {
    const user = userEvent.setup();
    vi.mocked(api.drawing).mockResolvedValue(DRAWING_DETAIL);
    renderDetail();

    await user.click(
      await screen.findByRole("checkbox", { name: /ground-truth overlay/i }),
    );

    // Every page card now points at the on-demand GT overlay PNG (a page with no imported
    // ground truth returns the plain page from that same route — no special-casing here).
    expect(screen.getByAltText("Page 1")).toHaveAttribute(
      "src",
      "/api/drawings/3/pages/1/gt-overlay",
    );
    expect(screen.getByAltText("Page 2")).toHaveAttribute(
      "src",
      "/api/drawings/3/pages/2/gt-overlay",
    );

    // The class-coloured boxes are keyed by the shared per-label legend.
    const legend = screen.getByRole("list", { name: "Overlay colour legend" });
    for (const label of ["cabinet", "countertop", "elevation"]) {
      expect(within(legend).getByText(label)).toBeInTheDocument();
    }

    // Toggling back off returns to the plain page image.
    await user.click(screen.getByRole("checkbox", { name: /ground-truth overlay/i }));
    expect(screen.getByAltText("Page 1")).toHaveAttribute(
      "src",
      "/api/drawings/3/pages/1/image",
    );
  });

  it("opens the GT overlay variant in the lightbox when the toggle is on", async () => {
    const user = userEvent.setup();
    vi.mocked(api.drawing).mockResolvedValue(DRAWING_DETAIL);
    renderDetail();

    await user.click(
      await screen.findByRole("checkbox", { name: /ground-truth overlay/i }),
    );
    // Clicking a page opens the scope-4 lightbox at the overlay image, for zoom/pan.
    await user.click(screen.getByAltText("Page 1"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("img")).toHaveAttribute(
      "src",
      "/api/drawings/3/pages/1/gt-overlay",
    );
  });

  it("mounts the ground-truth entry points (counting form + location import)", async () => {
    vi.mocked(api.drawing).mockResolvedValue(DRAWING_DETAIL);
    renderDetail();

    expect(
      await screen.findByRole("heading", { name: "Ground truth" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Counting totals")).toBeInTheDocument();
    expect(screen.getByText("Location boxes (JSON import)")).toBeInTheDocument();
  });

  it("scrolls to the ground-truth section when linked with the #ground-truth hash", async () => {
    // jsdom doesn't implement scrollIntoView; install a spy so the hash-scroll effect is
    // observable (the CTAs from the Leaderboard/Result land on this section, ADR 0011).
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    vi.mocked(api.drawing).mockResolvedValue(DRAWING_DETAIL);
    renderDetail("/library/drawings/3#ground-truth");

    await screen.findByRole("heading", { name: "Ground truth" });
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  });

  it("deletes the drawing after a confirmation stating the collateral", async () => {
    const user = userEvent.setup();
    vi.mocked(api.drawing).mockResolvedValue(DRAWING_DETAIL);
    vi.mocked(api.deleteDrawing).mockResolvedValue({ runs: 2, results: 5 });
    renderDetail();

    await user.click(
      await screen.findByRole("button", { name: "Delete drawing" }),
    );

    // The confirmation states the collateral (2 runs / 5 results) and the irreversible warning.
    expect(
      await screen.findByText(/2 runs \/ 5 results that used it/i),
    ).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();

    // Confirm — the dialog's own destructive button, not the header trigger.
    const confirm = screen
      .getAllByRole("button", { name: "Delete drawing" })
      .at(-1)!;
    await user.click(confirm);

    await waitFor(() => expect(api.deleteDrawing).toHaveBeenCalledWith(3));
    // On success we route back to the Drawings list.
    expect(await screen.findByText("drawings list page")).toBeInTheDocument();
  });

  it("treats a non-numeric id as not found without fetching", async () => {
    renderDetail("/library/drawings/not-a-number");

    expect(await screen.findByText("Drawing not found")).toBeInTheDocument();
    expect(api.drawing).not.toHaveBeenCalled();
  });

  it("surfaces the API error for an unknown drawing", async () => {
    const { ApiError } = await vi.importActual<typeof import("@/api")>("@/api");
    vi.mocked(api.drawing).mockRejectedValue(
      new ApiError(404, "Drawing not found"),
    );
    renderDetail("/library/drawings/999");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Drawing not found",
    );
  });
});
