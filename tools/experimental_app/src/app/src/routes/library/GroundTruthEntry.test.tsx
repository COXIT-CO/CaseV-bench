import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GroundTruthEntry } from "@/routes/library/GroundTruthEntry";
import { renderWithProviders } from "@/test/render";
import { LOCATION_IMPORT_RESULT } from "@/test/fixtures";

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: { importLocationGroundTruth: vi.fn() },
  };
});
import { api } from "@/api";

function renderEntry() {
  return renderWithProviders(<GroundTruthEntry drawingId={3} />);
}

describe("GroundTruthEntry — location import", () => {
  beforeEach(() => {
    vi.mocked(api.importLocationGroundTruth).mockReset();
  });

  it("imports a native JSON file and reports the created count and problems", async () => {
    vi.mocked(api.importLocationGroundTruth).mockResolvedValue(
      LOCATION_IMPORT_RESULT,
    );
    renderEntry();

    const file = new File(['{"objects":[]}'], "gt.json", {
      type: "application/json",
    });
    await userEvent.upload(await screen.findByLabelText("Location JSON file"), file);
    await userEvent.click(screen.getByRole("button", { name: "Import JSON" }));

    // The import does exactly one thing — the file, and nothing else (ADR 0032).
    await waitFor(() =>
      expect(api.importLocationGroundTruth).toHaveBeenCalledWith(3, file),
    );
    // The problem report surfaces the created count and both reported problems.
    expect(await screen.findByText(/Imported 37 boxes\./)).toBeInTheDocument();
    expect(screen.getByText(/no taxonomy mapping for label 'windows'/)).toBeInTheDocument();
    expect(
      screen.getByText(/which drawing 3 does not have/),
    ).toBeInTheDocument();
  });

  it("offers the importer alone — no counting totals form, no derive checkbox", async () => {
    renderEntry();

    await screen.findByLabelText("Location JSON file");
    expect(screen.queryByText("Counting totals")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save totals" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("cabinet")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("surfaces the API error when an import is rejected", async () => {
    const { ApiError } = await vi.importActual<typeof import("@/api")>("@/api");
    vi.mocked(api.importLocationGroundTruth).mockRejectedValue(
      new ApiError(400, "file is not valid JSON"),
    );
    renderEntry();

    const file = new File(["nope"], "gt.json", { type: "application/json" });
    await userEvent.upload(await screen.findByLabelText("Location JSON file"), file);
    await userEvent.click(screen.getByRole("button", { name: "Import JSON" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "file is not valid JSON",
    );
  });
});
