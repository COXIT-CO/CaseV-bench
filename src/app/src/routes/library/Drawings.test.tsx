import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Drawings } from "@/routes/library/Drawings";
import { renderWithProviders } from "@/test/render";
import { DRAWINGS_LIST } from "@/test/fixtures";

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: { drawings: vi.fn(), uploadDrawing: vi.fn() },
  };
});
import { api } from "@/api";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="url">{loc.pathname}</div>;
}

function renderDrawings() {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/library/drawings" element={<Drawings />} />
        <Route path="/library/drawings/:id" element={<div>detail page</div>} />
      </Routes>
      <LocationProbe />
    </>,
    { route: "/library/drawings" },
  );
}

describe("Drawings list", () => {
  beforeEach(() => {
    vi.mocked(api.drawings).mockReset();
    vi.mocked(api.uploadDrawing).mockReset();
  });

  it("lists drawings with their page counts, each linking to its detail", async () => {
    vi.mocked(api.drawings).mockResolvedValue(DRAWINGS_LIST);
    renderDrawings();

    const first = await screen.findByText("prj0001");
    expect(screen.getByText("4 pages")).toBeInTheDocument();
    // A single-page drawing is pluralized correctly.
    expect(screen.getByText("1 page")).toBeInTheDocument();

    await userEvent.click(first);
    expect(await screen.findByText("detail page")).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent("/library/drawings/3");
  });

  it("guides the user to upload when there are no drawings", async () => {
    vi.mocked(api.drawings).mockResolvedValue({ drawings: [] });
    renderDrawings();

    expect(await screen.findByText("No drawings yet")).toBeInTheDocument();
  });

  it("uploads a PDF and routes to the created drawing's detail", async () => {
    vi.mocked(api.drawings).mockResolvedValue({ drawings: [] });
    vi.mocked(api.uploadDrawing).mockResolvedValue({
      id: 9,
      name: "uploaded",
      page_count: 2,
    });
    renderDrawings();

    await screen.findByText("No drawings yet");
    const file = new File(["%PDF-1.4"], "uploaded.pdf", {
      type: "application/pdf",
    });
    await userEvent.upload(screen.getByLabelText("PDF file"), file);
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => expect(api.uploadDrawing).toHaveBeenCalledWith(file));
    expect(await screen.findByText("detail page")).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent("/library/drawings/9");
  });

  it("keeps upload disabled until a file is chosen", async () => {
    vi.mocked(api.drawings).mockResolvedValue({ drawings: [] });
    renderDrawings();

    await screen.findByText("No drawings yet");
    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
  });

  it("surfaces the API error message when an upload fails", async () => {
    const { ApiError } = await vi.importActual<typeof import("@/api")>("@/api");
    vi.mocked(api.drawings).mockResolvedValue({ drawings: [] });
    vi.mocked(api.uploadDrawing).mockRejectedValue(
      new ApiError(400, "Could not read the PDF"),
    );
    renderDrawings();

    await screen.findByText("No drawings yet");
    const file = new File(["x"], "bad.pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("PDF file"), file);
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByText("Could not read the PDF")).toBeInTheDocument();
  });
});
