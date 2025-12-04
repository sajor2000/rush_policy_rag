"use client";

import { Card } from "@/components/ui/card";
import { Lightbulb, MessageSquare, Search, FileText } from "lucide-react";

const tips = [
  {
    icon: MessageSquare,
    title: "Ask a specific question",
    description: "Direct questions find better matches than vague topics. Ask exactly what you need to know.",
    example: "\"What are the dwell time limits for peripheral IVs?\"",
  },
  {
    icon: Search,
    title: "Name the RUSH entity",
    description: "For entity-specific answers, include RUMC, RUMG, RMG, ROPH, RCMC, or RCH in your question.",
    example: "\"What is the visitor policy for ROPH pediatric units?\"",
  },
  {
    icon: Lightbulb,
    title: "Abbreviations work",
    description: "Use medical shorthand naturally—ED, ICU, SBAR, PIV, CLABSI, and 150+ others are understood.",
    example: "\"ED triage protocol for chest pain\"",
  },
  {
    icon: FileText,
    title: "Check Supporting Evidence",
    description: "Quick Answer summarizes; Supporting Evidence shows exact policy text with page numbers.",
    example: "\"Click numbered citations to see the source.\"",
  },
];

export default function PromptingTips() {
  return (
    <section className="w-full py-8 md:py-12">
      <div className="container max-w-6xl mx-auto px-4 md:px-6">
        <h2 className="text-2xl md:text-3xl font-semibold text-foreground mb-8 text-center">
          Let&apos;s find what you need
        </h2>
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {tips.map((tip, index) => {
            const Icon = tip.icon;
            return (
              <Card
                key={index}
                className="p-6 hover-elevate"
                data-testid={`card-tip-${index}`}
              >
                <div className="flex gap-4">
                  <div className="flex-shrink-0">
                    <div className="w-10 h-10 rounded-md bg-rush-sage flex items-center justify-center">
                      <Icon className="h-5 w-5 text-rush-legacy" />
                    </div>
                  </div>
                  <div className="flex-1 space-y-1">
                    <h3 className="font-semibold text-foreground">{tip.title}</h3>
                    <p className="text-sm text-muted-foreground">{tip.description}</p>
                    <p className="text-sm text-rush-legacy italic font-serif pt-1">
                      {tip.example}
                    </p>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      </div>
    </section>
  );
}
