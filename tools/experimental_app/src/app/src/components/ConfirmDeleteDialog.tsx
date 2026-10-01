import * as React from "react";

import { ErrorBlock } from "@/components/states";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

// The shared confirm-with-counts modal for destructive deletes (ADR-0016, ticket 07,
// reused by the Drawing/Prompt deletes in tickets 08/09). The caller supplies the collateral
// counts as `description`; this dialog always spells out that the action is irreversible and
// gates it behind a destructive confirm button that shows the in-flight state. On a
// successful confirm it closes itself; on failure it stays open and surfaces the error.

export function ConfirmDeleteDialog({
  trigger,
  title,
  description,
  confirmLabel = "Delete permanently",
  onConfirm,
  pending = false,
  error,
}: {
  /** The element that opens the dialog (e.g. a destructive "Delete" button). */
  trigger: React.ReactNode;
  title: string;
  /** The collateral the caller wants stated up front — the counts of what will be removed. */
  description: React.ReactNode;
  confirmLabel?: string;
  /** Runs the delete; resolve to close the dialog, reject to keep it open with `error` shown. */
  onConfirm: () => Promise<unknown>;
  pending?: boolean;
  error?: unknown;
}) {
  const [open, setOpen] = React.useState(false);

  async function handleConfirm() {
    try {
      await onConfirm();
      setOpen(false);
    } catch {
      // The mutation error is surfaced via `error`; keep the dialog open to show it.
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <p className="text-[13px] font-medium text-destructive">
          This cannot be undone.
        </p>

        {error != null && <ErrorBlock error={error} />}

        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setOpen(false)}
            disabled={pending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={handleConfirm}
            disabled={pending}
          >
            {pending ? "Deleting…" : confirmLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
