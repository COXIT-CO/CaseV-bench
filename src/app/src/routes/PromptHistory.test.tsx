import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PromptHistory } from "@/routes/PromptHistory";
import { renderWithProviders } from "@/test/render";
import { PROMPT_HISTORY, PROMPT_HISTORY_SINGLE } from "@/test/fixtures";

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: {
      promptHistory: vi.fn(),
      appendPromptVersion: vi.fn(),
      deletePromptVersion: vi.fn(),
      deletePromptFamily: vi.fn(),
    },
  };
});
import { api } from "@/api";

function renderHistory(route = "/prompts/counting/cabinet-count-v2") {
  return renderWithProviders(
    <Routes>
      <Route path="/prompts/:task/:family" element={<PromptHistory />} />
      <Route path="/prompts" element={<div>prompts list</div>} />
    </Routes>,
    { route },
  );
}

describe("PromptHistory", () => {
  beforeEach(() => {
    vi.mocked(api.promptHistory).mockReset();
    vi.mocked(api.appendPromptVersion).mockReset();
    vi.mocked(api.deletePromptVersion).mockReset();
    vi.mocked(api.deletePromptFamily).mockReset();
  });

  it("defaults the compare panes to the two newest versions", async () => {
    vi.mocked(api.promptHistory).mockResolvedValue(PROMPT_HISTORY);
    renderHistory();

    // Version A (left) defaults to the previous version, Version B (right) to the newest.
    const paneA = (await screen.findByLabelText("Version A")) as HTMLSelectElement;
    const paneB = screen.getByLabelText("Version B") as HTMLSelectElement;
    expect(paneA.value).toBe("1");
    expect(paneB.value).toBe("2");
    // Scope to the compare region so the edit form's prefilled textarea (which holds the
    // latest text too) doesn't collide with the v2 pane.
    const compare = screen.getByRole("region", { name: "Compare versions" });
    expect(
      within(compare).getByText("Count every cabinet in the drawing."),
    ).toBeInTheDocument();
    expect(
      within(compare).getByText("Count only base cabinets, ignore wall cabinets."),
    ).toBeInTheDocument();
  });

  it("swaps a pane's text when a different version is picked", async () => {
    vi.mocked(api.promptHistory).mockResolvedValue(PROMPT_HISTORY);
    renderHistory();

    // Point Version B at v1 too: both panes now read the v1 text (scoped to the compare
    // region so the edit form's prefilled textarea isn't counted).
    await userEvent.selectOptions(await screen.findByLabelText("Version B"), "1");
    const compare = screen.getByRole("region", { name: "Compare versions" });
    await waitFor(() =>
      expect(
        within(compare).getAllByText("Count every cabinet in the drawing."),
      ).toHaveLength(2),
    );
  });

  it("appends a new immutable version from the edit form", async () => {
    vi.mocked(api.promptHistory).mockResolvedValue(PROMPT_HISTORY);
    vi.mocked(api.appendPromptVersion).mockResolvedValue({
      task: "counting",
      family: "cabinet-count-v2",
      version: 3,
    });
    renderHistory();

    const textarea = await screen.findByLabelText("Prompt text");
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "Count base + wall cabinets separately.");
    await userEvent.click(
      screen.getByRole("button", { name: "Save as new version" }),
    );

    await waitFor(() =>
      expect(api.appendPromptVersion).toHaveBeenCalledWith(
        "counting",
        "cabinet-count-v2",
        "Count base + wall cabinets separately.",
      ),
    );
  });

  it("disables save until the text differs from the latest version", async () => {
    vi.mocked(api.promptHistory).mockResolvedValue(PROMPT_HISTORY);
    renderHistory();

    // Prefilled with the latest text ⇒ no change yet ⇒ save disabled.
    const save = await screen.findByRole("button", { name: "Save as new version" });
    expect(save).toBeDisabled();
  });

  it("reads a single-version family (compare against itself)", async () => {
    vi.mocked(api.promptHistory).mockResolvedValue(PROMPT_HISTORY_SINGLE);
    renderHistory("/prompts/location/default");

    const paneA = (await screen.findByLabelText("Version A")) as HTMLSelectElement;
    const paneB = screen.getByLabelText("Version B") as HTMLSelectElement;
    expect(paneA.value).toBe("1");
    expect(paneB.value).toBe("1");
    // Both compare panes read v1 (the edit textarea is excluded by scoping to the region).
    const compare = screen.getByRole("region", { name: "Compare versions" });
    expect(
      within(compare).getAllByText("Return bounding boxes for every fixture."),
    ).toHaveLength(2);
  });

  it("shows an error block when the family is unknown", async () => {
    const { ApiError } = await vi.importActual<typeof import("@/api")>("@/api");
    vi.mocked(api.promptHistory).mockRejectedValue(
      new ApiError(404, "no prompt family 'ghost' for task counting"),
    );
    renderHistory("/prompts/counting/ghost");

    expect(
      await screen.findByText(/no prompt family 'ghost'/),
    ).toBeInTheDocument();
  });

  it("treats an invalid task in the URL as not found", async () => {
    renderHistory("/prompts/bogus/default");
    expect(await screen.findByText("Prompt not found")).toBeInTheDocument();
    // The bad-task guard short-circuits before any fetch.
    expect(api.promptHistory).not.toHaveBeenCalled();
  });

  it("deletes the whole family after a confirmation stating the family collateral", async () => {
    const user = userEvent.setup();
    vi.mocked(api.promptHistory).mockResolvedValue(PROMPT_HISTORY);
    vi.mocked(api.deletePromptFamily).mockResolvedValue({ runs: 3, results: 8 });
    renderHistory();

    await user.click(await screen.findByRole("button", { name: "Delete family" }));

    // The confirm states the family total (3 runs / 8 results) and the irreversible warning.
    expect(
      await screen.findByText(/3 runs \/ 8 results that pinned any of them/i),
    ).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();

    // Confirm — the dialog's own destructive button, not the header trigger.
    const confirm = screen
      .getAllByRole("button", { name: "Delete family" })
      .at(-1)!;
    await user.click(confirm);

    await waitFor(() =>
      expect(api.deletePromptFamily).toHaveBeenCalledWith(
        "counting",
        "cabinet-count-v2",
      ),
    );
    // On success we route back to the Prompts list.
    expect(await screen.findByText("prompts list")).toBeInTheDocument();
  });

  it("deletes a single mid-lineage version and stays on the still-populated family", async () => {
    const user = userEvent.setup();
    vi.mocked(api.promptHistory).mockResolvedValue(PROMPT_HISTORY);
    vi.mocked(api.deletePromptVersion).mockResolvedValue({ runs: 2, results: 5 });
    renderHistory();

    await user.click(await screen.findByRole("button", { name: "Delete v2" }));

    // The confirm states this version's own collateral (2 runs / 5 results).
    expect(
      await screen.findByText(/2 runs \/ 5 results that pinned it/i),
    ).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Delete v2" }).at(-1)!);

    await waitFor(() =>
      expect(api.deletePromptVersion).toHaveBeenCalledWith(
        "counting",
        "cabinet-count-v2",
        2,
      ),
    );
    // The family still has other versions, so we stay put (no route to the list).
    expect(screen.queryByText("prompts list")).not.toBeInTheDocument();
  });

  it("routes to the list when the deleted version was the family's last", async () => {
    const user = userEvent.setup();
    vi.mocked(api.promptHistory).mockResolvedValue(PROMPT_HISTORY_SINGLE);
    vi.mocked(api.deletePromptVersion).mockResolvedValue({ runs: 0, results: 0 });
    renderHistory("/prompts/location/default");

    await user.click(await screen.findByRole("button", { name: "Delete v1" }));
    await user.click(screen.getAllByRole("button", { name: "Delete v1" }).at(-1)!);

    await waitFor(() =>
      expect(api.deletePromptVersion).toHaveBeenCalledWith(
        "location",
        "default",
        1,
      ),
    );
    // Deleting the only version leaves nothing to show, so we route back to the list.
    expect(await screen.findByText("prompts list")).toBeInTheDocument();
  });
});
