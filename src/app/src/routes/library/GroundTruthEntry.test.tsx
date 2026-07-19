import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GroundTruthEntry } from "@/routes/library/GroundTruthEntry";
import { renderWithProviders } from "@/test/render";
import {
  COUNTING_GT,
  COUNTING_GT_EMPTY,
  LOCATION_IMPORT_RESULT,
} from "@/test/fixtures";

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: {
      countingGroundTruth: vi.fn(),
      saveCountingGroundTruth: vi.fn(),
      importLocationGroundTruth: vi.fn(),
    },
  };
});
import { api } from "@/api";

function renderEntry() {
  return renderWithProviders(<GroundTruthEntry drawingId={3} />);
}

describe("GroundTruthEntry — counting form", () => {
  beforeEach(() => {
    vi.mocked(api.countingGroundTruth).mockReset();
    vi.mocked(api.saveCountingGroundTruth).mockReset();
  });

  it("pre-fills the stored totals and upserts them on save", async () => {
    vi.mocked(api.countingGroundTruth).mockResolvedValue(COUNTING_GT);
    vi.mocked(api.saveCountingGroundTruth).mockResolvedValue(COUNTING_GT);
    renderEntry();

    // The cabinets input is seeded from the pre-fill (4); an unentered label stays blank.
    const cabinets = await screen.findByLabelText("cabinet");
    expect(cabinets).toHaveValue(4);
    expect(screen.getByLabelText("elevation_callout")).toHaveValue(null);

    // Fill the last label, then save — the PUT carries every label as an integer.
    await userEvent.type(screen.getByLabelText("elevation_callout"), "0");
    await userEvent.click(screen.getByRole("button", { name: "Save totals" }));

    await waitFor(() =>
      expect(api.saveCountingGroundTruth).toHaveBeenCalledWith(3, {
        cabinet: 4,
        countertop: 2,
        elevation: 1,
        elevation_callout: 0,
      }),
    );
    expect(
      await screen.findByText(/results for this drawing are now scored/i),
    ).toBeInTheDocument();
  });

  it("disables save until every label has an integer", async () => {
    vi.mocked(api.countingGroundTruth).mockResolvedValue(COUNTING_GT_EMPTY);
    renderEntry();

    const save = await screen.findByRole("button", { name: "Save totals" });
    // Nothing entered yet → cannot submit.
    expect(save).toBeDisabled();

    for (const label of [
      "cabinet",
      "countertop",
      "elevation",
      "elevation_callout",
    ]) {
      await userEvent.type(screen.getByLabelText(label), "0");
    }
    expect(save).toBeEnabled();
  });

  it("surfaces the API error when a save is rejected", async () => {
    const { ApiError } = await vi.importActual<typeof import("@/api")>("@/api");
    vi.mocked(api.countingGroundTruth).mockResolvedValue(COUNTING_GT);
    vi.mocked(api.saveCountingGroundTruth).mockRejectedValue(
      new ApiError(400, "A total is required for every label"),
    );
    renderEntry();

    await userEvent.type(await screen.findByLabelText("elevation_callout"), "0");
    await userEvent.click(screen.getByRole("button", { name: "Save totals" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A total is required for every label",
    );
  });
});

describe("GroundTruthEntry — location import", () => {
  beforeEach(() => {
    vi.mocked(api.countingGroundTruth).mockReset();
    vi.mocked(api.countingGroundTruth).mockResolvedValue(COUNTING_GT);
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

    // The derive-counting opt-in is off by default (ADR 0025).
    await waitFor(() =>
      expect(api.importLocationGroundTruth).toHaveBeenCalledWith(3, file, false),
    );
    // The problem report surfaces the created count and both reported problems.
    expect(await screen.findByText(/Imported 37 boxes\./)).toBeInTheDocument();
    expect(screen.getByText(/no taxonomy mapping for label 'windows'/)).toBeInTheDocument();
    expect(
      screen.getByText(/which drawing 3 does not have/),
    ).toBeInTheDocument();
  });

  it("passes the derive-counting opt-in when the checkbox is ticked", async () => {
    vi.mocked(api.importLocationGroundTruth).mockResolvedValue(
      LOCATION_IMPORT_RESULT,
    );
    renderEntry();

    const file = new File(['{"objects":[]}'], "gt.json", {
      type: "application/json",
    });
    await userEvent.upload(await screen.findByLabelText("Location JSON file"), file);
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: /also set counting ground truth from these boxes/i,
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Import JSON" }));

    await waitFor(() =>
      expect(api.importLocationGroundTruth).toHaveBeenCalledWith(3, file, true),
    );
  });
});
