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
        <Route path="/prompts/:family" element={<div>history page</div>} />
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

  it("lists every family flat, with its latest version and count", async () => {
    vi.mocked(api.prompts).mockResolvedValue(PROMPTS);
    renderPrompts();

    expect(await screen.findByText("boxes")).toBeInTheDocument();
    expect(screen.getByText("latest v3")).toBeInTheDocument();
    expect(screen.getByText("3 versions")).toBeInTheDocument();
    expect(screen.getByText("default")).toBeInTheDocument();
    // No task grouping: no section heading wraps the families.
    expect(screen.queryByRole("heading", { name: "counting" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "location" })).not.toBeInTheDocument();
  });

  it("links a family to its history page", async () => {
    vi.mocked(api.prompts).mockResolvedValue(PROMPTS);
    renderPrompts();

    await userEvent.click(await screen.findByText("boxes"));
    expect(await screen.findByText("history page")).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent("/prompts/boxes");
  });

  it("creates a new family without a task choice and routes to its history page", async () => {
    vi.mocked(api.prompts).mockResolvedValue(PROMPTS);
    vi.mocked(api.createPrompt).mockResolvedValue({
      family: "fixtures",
      version: 1,
    });
    renderPrompts();

    await screen.findByText("boxes");
    // The authoring form asks only for what varies — there is no Task control to pick.
    expect(screen.queryByLabelText("Task")).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Family"), "fixtures");
    await userEvent.type(screen.getByLabelText("Prompt text"), "Locate fixtures");
    await userEvent.click(screen.getByRole("button", { name: "Create prompt" }));

    await waitFor(() =>
      expect(api.createPrompt).toHaveBeenCalledWith(
        { family: "fixtures", text: "Locate fixtures" },
        expect.anything(),
      ),
    );
    expect(await screen.findByText("history page")).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent("/prompts/fixtures");
  });

  it("surfaces the service message when creating a duplicate family", async () => {
    const { ApiError } = await vi.importActual<typeof import("@/api")>("@/api");
    vi.mocked(api.prompts).mockResolvedValue(PROMPTS);
    vi.mocked(api.createPrompt).mockRejectedValue(
      new ApiError(400, "prompt family 'default' already exists"),
    );
    renderPrompts();

    await screen.findByText("boxes");
    await userEvent.type(screen.getByLabelText("Family"), "default");
    await userEvent.type(screen.getByLabelText("Prompt text"), "dupe");
    await userEvent.click(screen.getByRole("button", { name: "Create prompt" }));

    expect(
      await screen.findByText(/prompt family 'default' already exists/),
    ).toBeInTheDocument();
  });
});
