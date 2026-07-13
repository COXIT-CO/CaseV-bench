import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "@/App";
import { ThemeProvider } from "@/lib/theme";
// Inter (sans) + JetBrains Mono (mono) — the mockup's type system (self-hosted so dev
// works offline). Imported before index.css so Tailwind's font-family tokens resolve.
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "@/index.css";

// One QueryClient for the app. Defaults tuned for an internal tool: don't refetch on every
// window focus, and treat data as fresh briefly so navigation feels instant.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 30_000,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
