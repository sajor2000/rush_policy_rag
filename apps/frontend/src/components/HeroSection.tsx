"use client";

import { Button } from "@/components/ui/button";
import { ArrowDown } from "lucide-react";

export default function HeroSection({ onGetStarted }: { onGetStarted: () => void }) {
  return (
    <section className="w-full py-12 md:py-16 lg:py-20">
      <div className="container max-w-5xl mx-auto px-4 md:px-6">
        <div className="flex flex-col items-center text-center space-y-6">
          <h1 className="text-4xl md:text-5xl lg:text-6xl font-semibold text-rush-legacy leading-tight">
            Policy answers when you need them most.
          </h1>
          <p className="text-2xl md:text-3xl text-foreground font-medium">
            Ask a question. Get the answer with the source.
          </p>

          <p className="text-lg md:text-xl text-muted-foreground max-w-2xl">
            Connect with the knowledge you need from 1,800+ RUSH policies instantly. Every answer includes a quick summary
            and the exact policy text so you can act with confidence.
          </p>

          <div className="flex flex-col sm:flex-row gap-4 pt-4">
            <Button
              size="lg"
              onClick={onGetStarted}
              className="bg-rush-legacy hover:bg-rush-legacy text-lg px-8"
              data-testid="button-get-started"
            >
              Let&apos;s get started
            </Button>
          </div>

          <button
            onClick={onGetStarted}
            className="mt-8 flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors"
            data-testid="button-scroll-down"
          >
            <span className="text-sm">Start asking questions</span>
            <ArrowDown className="h-4 w-4 animate-bounce" />
          </button>
        </div>
      </div>
    </section>
  );
}
