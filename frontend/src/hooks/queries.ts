import { useQuery } from "@tanstack/react-query";

import { api } from "@/api";

// React Query hooks keyed per Part-A endpoint. Slice 0 has just the proof query; feature
// hooks (leaderboard, result, runs, …) are added by their own slices.

export function useMeta() {
  return useQuery({ queryKey: ["meta"], queryFn: api.meta });
}
