"use client";

import { Card } from "@/components/ui/card";
import { Lightbulb, MessageSquare, Search, FileText } from "lucide-react";

const tips = [
  {
    icon: MessageSquare,
    title: "Set the scene",
    description: "Mention the role, unit, and situation so the Quick Answer is laser focused.",
    example: "\"RUMC ICU charge nurse onboarding requirements\"",
  },
  {
    icon: Search,
    title: "Call out entity & timeframe",
    description: "Include the RUSH entity (RUMC/RMG/ROPH/RCMC) and any timing or status cues.",
    example: "\"Visitor rules for ROPH after 10pm\"",
  },
  {
    icon: FileText,
    title: "Use both sections",
    description: "Read the Quick Answer, then scan the Supporting Evidence snippets for context.",
    example: "\"Who can accept verbal orders? Show the proof.\"",
  },
  {
    icon: Lightbulb,
    title: "Request more proof",
    description: "Need another citation? Ask for more evidence or a different section.",
    example: "\"Show me additional sections on medication reconciliation\"",
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
