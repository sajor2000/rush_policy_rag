"use client";

import { Button } from "@/components/ui/button";
import { AlertCircle } from "lucide-react";

interface ErrorMessageProps {
  message?: string;
  onRetry?: () => void;
}

export default function ErrorMessage({ 
  message = "We hit a snag—but we're on it. Let's try that again.", 
  onRetry 
}: ErrorMessageProps) {
  return (
    <div 
      className="flex flex-col items-center gap-4 p-6 bg-card border border-card-border rounded-lg max-w-md mx-auto"
      data-testid="error-message"
    >
      <div className="w-12 h-12 rounded-full bg-rush-sage flex items-center justify-center">
        <AlertCircle className="h-6 w-6 text-rush-legacy" />
      </div>
      <div className="text-center space-y-2">
        <h3 className="font-semibold text-foreground">We&apos;re working on it</h3>
        <p className="text-sm text-muted-foreground">{message}</p>
      </div>
      {onRetry && (
        <Button
          onClick={onRetry}
          className="bg-rush-vitality hover:bg-rush-vitality text-rush-legacy font-semibold"
          data-testid="button-retry"
        >
          Let&apos;s try again
        </Button>
      )}
    </div>
  );
}
