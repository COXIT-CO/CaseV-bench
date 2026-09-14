import { cn } from "@/lib/utils";

/** shadcn skeleton — the shared loading primitive (spec §B.5). */
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("animate-pulse rounded-md bg-muted", className)} {...props} />
  );
}

export { Skeleton };
