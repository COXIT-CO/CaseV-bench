import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Models } from "@/routes/library/Models";
import { renderWithProviders } from "@/test/render";
import { MODELS } from "@/test/fixtures";

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return { ...actual, api: { models: vi.fn() } };
});
import { api } from "@/api";

describe("Models catalog", () => {
  beforeEach(() => {
    vi.mocked(api.models).mockReset();
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
});
