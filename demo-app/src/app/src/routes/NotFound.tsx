import { Link } from "react-router-dom";

import { EmptyState } from "@/components/states";
import { Button } from "@/components/ui/button";

export function NotFound() {
  return (
    <EmptyState
      title="Page not found"
      description="That route doesn't exist."
      action={
        <Button asChild variant="outline" size="sm">
          <Link to="/">Back to Leaderboard</Link>
        </Button>
      }
    />
  );
}
