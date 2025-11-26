"use client";

import { Loader2 } from "lucide-react";

export default function LoadingState() {
  return (
    <div className="flex items-center gap-3 text-muted-foreground" data-testid="loading-state">
      <Loader2 className="h-5 w-5 animate-spin text-rush-growth" />
      <span className="text-base">On it—searching policies now...</span>
    </div>
  );
}
