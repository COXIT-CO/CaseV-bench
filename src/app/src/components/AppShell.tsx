import { ChevronDown } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { ThemeToggle } from "@/components/ThemeToggle";
import { ErrorBlock } from "@/components/states";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useMeta } from "@/hooks/queries";
import { cn } from "@/lib/utils";

// The persistent app shell (ADR 0011, spec §C.2): primary nav (Leaderboard · Runs ·
// Prompts), the secondary Library menu (Drawings, Models), and the light/dark toggle.
// Feature screens render into <Outlet/>.

const PRIMARY_NAV = [
  { to: "/", label: "Leaderboard", end: true },
  { to: "/runs", label: "Runs", end: false },
  { to: "/prompts", label: "Prompts", end: false },
];

const LIBRARY_NAV = [
  { to: "/library/drawings", label: "Drawings" },
  { to: "/library/models", label: "Models" },
];

function primaryLinkClass({ isActive }: { isActive: boolean }) {
  return cn(
    "rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
    isActive
      ? "bg-accent font-semibold text-foreground"
      : "font-medium text-muted-foreground hover:bg-accent hover:text-foreground",
  );
}

export function AppShell() {
  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="flex h-[52px] items-center gap-7 px-4">
          <NavLink
            to="/"
            className="flex items-center gap-2 text-[13px] font-semibold tracking-tight"
          >
            <span className="h-[18px] w-[18px] shrink-0 rounded bg-primary" />
            <span className="hidden sm:inline">Prompt &amp; Config Lab</span>
          </NavLink>

          <nav className="flex items-center gap-0.5" aria-label="Primary">
            {PRIMARY_NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={primaryLinkClass}
              >
                {item.label}
              </NavLink>
            ))}

            <DropdownMenu>
              <DropdownMenuTrigger className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-[13px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:bg-accent data-[state=open]:text-foreground">
                Library
                <ChevronDown className="h-3.5 w-3.5 opacity-60" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuLabel>Library</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {LIBRARY_NAV.map((item) => (
                  <DropdownMenuItem key={item.to} asChild>
                    <NavLink to={item.to}>{item.label}</NavLink>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <MetaStatus />
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}

// The slice-0 pipeline proof, kept live in the shell so every page confirms the SPA is
// talking to the JSON API through the dev proxy. Loading/error flow through the shared
// state primitives (spec §B.5).
function MetaStatus() {
  const { data, isLoading, isError, error } = useMeta();

  if (isLoading) {
    return <Skeleton className="h-4 w-28" aria-label="Loading API status" />;
  }
  if (isError) {
    return <ErrorBlock error={error} className="border-0 bg-transparent p-0" />;
  }
  return (
    <span
      className="hidden font-mono text-xs text-muted-foreground md:inline"
      title="Live counts from GET /api/meta"
    >
      {data!.drawing_count} drawings · {data!.run_count} runs ·{" "}
      {data!.result_count} results
    </span>
  );
}
