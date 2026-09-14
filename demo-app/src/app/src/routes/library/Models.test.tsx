import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Models } from "@/routes/library/Models";
import { renderWithProviders } from "@/test/render";
import { MODELS } from "@/test/fixtures";

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: { models: vi.fn(), addModel: vi.fn(), removeModel: vi.fn() },
  };
});
import { api } from "@/api";

describe("Models catalog", () => {
  beforeEach(() => {
    vi.mocked(api.models).mockReset();
    vi.mocked(api.addModel).mockReset();
    vi.mocked(api.removeModel).mockReset();
  });

  it("lists each curated model with its label and slug", async () => {
    vi.mocked(api.models).mockResolvedValue(MODELS);
    renderWithProviders(<Models />);

    expect(await screen.findByText("Claude Sonnet 4.5")).toBeInTheDocument();
    expect(
      screen.getByText("anthropic/claude-sonnet-4.5"),
    ).toBeInTheDocument();
    expect(screen.getByText("Gemini 2.5 Flash")).toBeInTheDocument();
  });

  it("shows an empty state when the catalog is empty", async () => {
    vi.mocked(api.models).mockResolvedValue({ catalog: [] });
    renderWithProviders(<Models />);

    expect(
      await screen.findByText("No models in the catalog"),
    ).toBeInTheDocument();
  });

  it("adds a model with its slug and label, then shows it after the refetch", async () => {
    const user = userEvent.setup();
    const added = { slug: "mistralai/pixtral-12b", label: "Pixtral 12B" };
    // First render shows the seeded three; after the add the refetch includes the new entry.
    vi.mocked(api.models)
      .mockResolvedValueOnce(MODELS)
      .mockResolvedValue({ catalog: [...MODELS.catalog, added] });
    vi.mocked(api.addModel).mockResolvedValue(added);
    renderWithProviders(<Models />);

    await screen.findByText("Claude Sonnet 4.5");
    await user.type(
      screen.getByLabelText("OpenRouter slug"),
      "  mistralai/pixtral-12b  ",
    );
    await user.type(screen.getByLabelText("Label"), "  Pixtral 12B  ");
    await user.click(screen.getByRole("button", { name: "Add model" }));

    // The slug/label are trimmed before they reach the API.
    await waitFor(() =>
      expect(api.addModel).toHaveBeenCalledWith(added, expect.anything()),
    );
    expect(await screen.findByText("Pixtral 12B")).toBeInTheDocument();
  });

  it("removes a model after confirming", async () => {
    const user = userEvent.setup();
    vi.mocked(api.models)
      .mockResolvedValueOnce(MODELS)
      .mockResolvedValue({
        catalog: MODELS.catalog.filter(
          (e) => e.slug !== "google/gemini-2.5-flash",
        ),
      });
    vi.mocked(api.removeModel).mockResolvedValue({
      slug: "google/gemini-2.5-flash",
      label: "Gemini 2.5 Flash",
    });
    renderWithProviders(<Models />);

    await screen.findByText("Gemini 2.5 Flash");
    // Each row has a Remove trigger; open the Gemini one (the last of the three).
    const removeTriggers = screen.getAllByRole("button", { name: "Remove" });
    await user.click(removeTriggers.at(-1)!);

    // The confirm names the entry and reassures that past runs are unaffected.
    expect(
      await screen.findByText(/past runs that used it are unaffected/i),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove model" }));

    await waitFor(() =>
      expect(api.removeModel).toHaveBeenCalledWith("google/gemini-2.5-flash"),
    );
    await waitFor(() =>
      expect(screen.queryByText("Gemini 2.5 Flash")).not.toBeInTheDocument(),
    );
  });
});
