import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DrawingDetail } from "@/routes/library/DrawingDetail";
import { renderWithProviders } from "@/test/render";
import { DRAWING_DETAIL } from "@/test/fixtures";

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return { ...actual, api: { drawing: vi.fn() } };
});
import { api } from "@/api";

function renderDetail(route = "/library/drawings/3") {
  return renderWithProviders(
    <Routes>
      <Route path="/library/drawings/:id" element={<DrawingDetail />} />
    </Routes>,
    { route },
  );
}

describe("DrawingDetail", () => {
  beforeEach(() => {
    vi.mocked(api.drawing).mockReset();
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
  });

  it("exposes the ground-truth entry point (consumed by ticket 07)", async () => {
    vi.mocked(api.drawing).mockResolvedValue(DRAWING_DETAIL);
    renderDetail();

    expect(
      await screen.findByRole("heading", { name: "Ground truth" }),
    ).toBeInTheDocument();
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
