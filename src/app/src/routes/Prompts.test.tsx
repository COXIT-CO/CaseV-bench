import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Prompts } from "@/routes/Prompts";
import { renderWithProviders } from "@/test/render";
import { PROMPTS } from "@/test/fixtures";

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: { prompts: vi.fn(), createPrompt: vi.fn() },
  };
});
import { api } from "@/api";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="url">{loc.pathname}</div>;
}

function renderPrompts() {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/prompts" element={<Prompts />} />
        <Route path="/prompts/:task/:family" element={<div>history page</div>} />
      </Routes>
      <LocationProbe />
    </>,
    { route: "/prompts" },
  );
}

describe("Prompts list", () => {
  beforeEach(() => {
    vi.mocked(api.prompts).mockReset();
    vi.mocked(api.createPrompt).mockReset();
  });

  it("groups families under their task with latest version and count", async () => {
    vi.mocked(api.prompts).mockResolvedValue(PROMPTS);
    renderPrompts();

    expect(await screen.findByText("cabinet-count-v2")).toBeInTheDocument();
    expect(screen.getByText("latest v3")).toBeInTheDocument();
    expect(screen.getByText("3 versions")).toBeInTheDocument();
    // Task-scoped groups: the counting and location headings both render.
    expect(screen.getByRole("heading", { name: "counting" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "location" })).toBeInTheDocument();
  });

  it("links a family to its history page", async () => {
    vi.mocked(api.prompts).mockResolvedValue(PROMPTS);
    renderPrompts();

    await userEvent.click(await screen.findByText("cabinet-count-v2"));
    expect(await screen.findByText("history page")).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent(
      "/prompts/counting/cabinet-count-v2",
    );
  });

  it("creates a new family and routes to its history page", async () => {
    vi.mocked(api.prompts).mockResolvedValue(PROMPTS);
    vi.mocked(api.createPrompt).mockResolvedValue({
      task: "location",
      family: "fixtures",
      version: 1,
    });
    renderPrompts();

    await screen.findByText("cabinet-count-v2");
    await userEvent.selectOptions(screen.getByLabelText("Task"), "location");
    await userEvent.type(screen.getByLabelText("Family"), "fixtures");
    await userEvent.type(screen.getByLabelText("Prompt text"), "Locate fixtures");
    await userEvent.click(screen.getByRole("button", { name: "Create prompt" }));

    await waitFor(() =>
      expect(api.createPrompt).toHaveBeenCalledWith(
        { task: "location", family: "fixtures", text: "Locate fixtures" },
        expect.anything(),
      ),
    );
    expect(await screen.findByText("history page")).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent("/prompts/location/fixtures");
  });

  it("surfaces the service message when creating a duplicate family", async () => {
    const { ApiError } = await vi.importActual<typeof import("@/api")>("@/api");
    vi.mocked(api.prompts).mockResolvedValue(PROMPTS);
    vi.mocked(api.createPrompt).mockRejectedValue(
      new ApiError(400, "prompt family 'default' already exists for task counting"),
    );
    renderPrompts();

    await screen.findByText("cabinet-count-v2");
    await userEvent.type(screen.getByLabelText("Family"), "default");
    await userEvent.type(screen.getByLabelText("Prompt text"), "dupe");
    await userEvent.click(screen.getByRole("button", { name: "Create prompt" }));

    expect(
      await screen.findByText(/prompt family 'default' already exists/),
    ).toBeInTheDocument();
  });
});
