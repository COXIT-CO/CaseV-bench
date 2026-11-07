import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDeleteDialog } from "@/components/ConfirmDeleteDialog";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/api";
import { renderWithProviders } from "@/test/render";

// The shared confirm-with-counts modal reused by every cascade delete (ADR-0016): it must
// state the collateral and the irreversible warning, gate the delete behind an explicit
// confirm, and stay open surfacing the error when the delete fails.

function renderDialog(
  props: Partial<React.ComponentProps<typeof ConfirmDeleteDialog>> = {},
) {
  return renderWithProviders(
    <ConfirmDeleteDialog
      trigger={<Button>Delete thing</Button>}
      title="Delete thing?"
      description="This removes the thing and its 3 dependents."
      onConfirm={vi.fn().mockResolvedValue(undefined)}
      {...props}
    />,
  );
}

describe("ConfirmDeleteDialog", () => {
  it("states the collateral and the irreversible warning once opened", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: "Delete thing" }));

    expect(
      await screen.findByText("This removes the thing and its 3 dependents."),
    ).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();
  });

  it("runs onConfirm and closes on success", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    renderDialog({ onConfirm });

    await user.click(screen.getByRole("button", { name: "Delete thing" }));
    await user.click(
      await screen.findByRole("button", { name: "Delete permanently" }),
    );

    expect(onConfirm).toHaveBeenCalledOnce();
    await waitFor(() =>
      expect(screen.queryByText("This cannot be undone.")).not.toBeInTheDocument(),
    );
  });

  it("stays open and surfaces the error when the delete fails", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn().mockRejectedValue(new Error("boom"));
    renderDialog({ onConfirm, error: new ApiError(500, "run is locked") });

    await user.click(screen.getByRole("button", { name: "Delete thing" }));
    await user.click(
      await screen.findByRole("button", { name: "Delete permanently" }),
    );

    // The dialog stays open (the warning is still there) and shows the API detail.
    expect(await screen.findByText("run is locked")).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();
  });

  it("shows the in-flight label while pending", async () => {
    const user = userEvent.setup();
    renderDialog({ pending: true });

    await user.click(screen.getByRole("button", { name: "Delete thing" }));

    expect(await screen.findByText("Deleting…")).toBeInTheDocument();
  });
});
