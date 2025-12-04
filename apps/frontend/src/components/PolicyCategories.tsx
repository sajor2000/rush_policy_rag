"use client";

import { Card } from "@/components/ui/card";
import { Stethoscope, Building2, GraduationCap } from "lucide-react";

const categories = [
  {
    icon: Stethoscope,
    title: "Clinical",
    description: "Patient care, safety protocols, medication administration, infection control, and clinical procedures.",
    examples: "IV therapy, fall prevention, restraints, code blue, transfusions",
  },
  {
    icon: Building2,
    title: "Operational",
    description: "HR, compliance, security, facilities, IT, and administrative procedures across all entities.",
    examples: "HIPAA, visitor policies, badge access, emergency codes, timekeeping",
  },
  {
    icon: GraduationCap,
    title: "University",
    description: "Academic policies, research compliance, student affairs, and faculty governance.",
    examples: "IRB protocols, academic integrity, student conduct, grant management",
  },
];

export default function PolicyCategories() {
  return (
    <section className="w-full py-6 md:py-8 bg-rush-sage/30">
      <div className="container max-w-6xl mx-auto px-4 md:px-6">
        <p className="text-sm font-medium text-muted-foreground text-center mb-4">
          Search across all PolicyTech categories
        </p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {categories.map((cat, index) => {
            const Icon = cat.icon;
            return (
              <Card
                key={index}
                className="p-4 bg-white/80 border-rush-legacy/10"
              >
                <div className="flex items-start gap-3">
                  <div className="flex-shrink-0">
                    <div className="w-8 h-8 rounded-md bg-rush-legacy/10 flex items-center justify-center">
                      <Icon className="h-4 w-4 text-rush-legacy" />
                    </div>
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-foreground text-sm">{cat.title}</h3>
                    <p className="text-xs text-muted-foreground mt-0.5">{cat.description}</p>
                    <p className="text-xs text-rush-legacy/70 mt-1 italic truncate" title={cat.examples}>
                      {cat.examples}
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
