import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LaunchRunForm } from "@/components/LaunchRun";
import { LAUNCH_OPTIONS } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

// The Advanced knobs (ticket 04): the form pre-fills sensible defaults for the common
// one-click launch, and lets a developer tune max_tokens + temperature — the latter a number
// or "provider default" (→ no temperature in the request).

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    api: { launchOptions: vi.fn(), createRun: vi.fn() },
  };
});
import { api } from "@/api";

function renderForm() {
  return renderWithProviders(<LaunchRunForm onLaunched={() => {}} />);
}

/** Pick a model so the form has a valid launch selection. */
async function pickModel() {
  await userEvent.click(
    await screen.findByRole("button", { name: "Claude Sonnet 4.5" }),
  );
}

async function launch() {
  await userEvent.click(screen.getByRole("button", { name: "Launch run" }));
}

describe("Launch form Advanced knobs", () => {
  beforeEach(() => {
    vi.mocked(api.launchOptions).mockReset();
    vi.mocked(api.createRun).mockReset();
    vi.mocked(api.launchOptions).mockResolvedValue(LAUNCH_OPTIONS);
    vi.mocked(api.createRun).mockResolvedValue({
      id: 1,
      status: "queued",
      task: "counting",
      total_units: 1,
    });
  });

  it("carries the pre-filled defaults for the one-click launch", async () => {
    renderForm();
    await pickModel();
    await launch();

    await waitFor(() =>
      expect(api.createRun).toHaveBeenCalledWith(
        expect.objectContaining({ max_tokens: 4096, temperature: 0 }),
        expect.anything(),
      ),
    );
  });

  it("submits the chosen max_tokens and temperature from the Advanced section", async () => {
    renderForm();
    await pickModel();
    await userEvent.click(screen.getByText("Advanced"));

    const maxTokens = screen.getByLabelText("max_tokens");
    await userEvent.clear(maxTokens);
    await userEvent.type(maxTokens, "8192");
    const temperature = screen.getByLabelText("temperature");
    await userEvent.clear(temperature);
    await userEvent.type(temperature, "0.7");

    await launch();

    await waitFor(() =>
      expect(api.createRun).toHaveBeenCalledWith(
        expect.objectContaining({ max_tokens: 8192, temperature: 0.7 }),
        expect.anything(),
      ),
    );
  });

  it("sends no temperature when 'provider default' is chosen", async () => {
    renderForm();
    await pickModel();
    await userEvent.click(screen.getByText("Advanced"));
    await userEvent.click(
      screen.getByLabelText("Use provider default (omit temperature)"),
    );

    await launch();

    await waitFor(() =>
      expect(api.createRun).toHaveBeenCalledWith(
        expect.objectContaining({ temperature: null }),
        expect.anything(),
      ),
    );
  });
});
