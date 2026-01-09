"use client";

import { Loader2, Search, Sparkles, CheckCircle } from "lucide-react";

interface LoadingStateProps {
  /** Current status message from streaming */
  status?: string;
  /** Whether response is being streamed (show streaming indicator) */
  isStreaming?: boolean;
}

/**
 * Loading state component with dynamic status updates.
 *
 * Shows pipeline progress during streaming:
 * - "Searching policies..." with search icon
 * - "Generating response..." with sparkles icon
 * - Default state with spinner
 */
export default function LoadingState({
  status,
  isStreaming = false,
}: LoadingStateProps) {
  // Determine icon based on status message
  const getStatusIcon = () => {
    if (!status) {
      return <Loader2 className="h-5 w-5 animate-spin text-rush-growth" />;
    }

    const lowerStatus = status.toLowerCase();

    if (lowerStatus.includes("search")) {
      return <Search className="h-5 w-5 text-rush-growth animate-pulse" />;
    }

    if (lowerStatus.includes("generat") || lowerStatus.includes("stream")) {
      return <Sparkles className="h-5 w-5 text-rush-growth animate-pulse" />;
    }

    if (lowerStatus.includes("complete") || lowerStatus.includes("done")) {
      return <CheckCircle className="h-5 w-5 text-rush-growth" />;
    }

    return <Loader2 className="h-5 w-5 animate-spin text-rush-growth" />;
  };

  const displayMessage = status || "On it—searching policies now...";

  return (
    <div
      className="flex items-center gap-3 text-muted-foreground"
      data-testid="loading-state"
    >
      {getStatusIcon()}
      <span className="text-base">{displayMessage}</span>
      {isStreaming && (
        <span className="ml-2 flex gap-1">
          <span className="w-1.5 h-1.5 bg-rush-growth rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
          <span className="w-1.5 h-1.5 bg-rush-growth rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
          <span className="w-1.5 h-1.5 bg-rush-growth rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
        </span>
      )}
    </div>
  );
}
