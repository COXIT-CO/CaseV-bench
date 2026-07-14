import { AlertCircle } from "lucide-react";
import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/api";
import { cn } from "@/lib/utils";

// Shared empty / loading / error primitives every screen reuses (spec §B.5). React
// Query's isLoading / isError drive these; the error block surfaces the API's `detail`.

export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-10 text-center",
        className,
      )}
    >
      <p className="text-sm font-medium">{title}</p>
      {description && (
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function LoadingBlock({
  rows = 3,
  className,
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <div
      className={cn("space-y-2", className)}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span className="sr-only">Loading…</span>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-8 w-full" aria-hidden="true" />
      ))}
    </div>
  );
}

/** Turns any thrown error into a readable block, unwrapping the API `detail` envelope. */
export function ErrorBlock({
  error,
  className,
}: {
  error: unknown;
  className?: string;
}) {
  const detail =
    error instanceof ApiError
      ? error.detail
      : error instanceof Error
        ? error.message
        : "Something went wrong.";
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive",
        className,
      )}
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{detail}</span>
    </div>
  );
}
