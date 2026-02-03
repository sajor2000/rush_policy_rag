import { Metadata } from "next";
import Link from "next/link";
import RushLogo from "@/components/RushLogo";

export const metadata: Metadata = {
  title: "Terms of Use | RUSH Policy Chat",
  description: "Terms of use for RUSH Policy Chat application",
};

export default function TermsOfUse() {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      <header className="sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur">
        <div className="container max-w-4xl mx-auto px-4 h-16 flex items-center">
          <Link href="/">
            <RushLogo />
          </Link>
        </div>
      </header>

      <main className="flex-1 container max-w-4xl mx-auto px-4 py-12">
        <h1 className="text-3xl font-semibold text-rush-legacy mb-8">Terms of Use</h1>

        <div className="prose prose-rush max-w-none space-y-6 text-rush-charcoal">
          <p>This application is provided by Rush University System for Health for internal employee use. By using this application, you agree to the following terms:</p>

          <section>
            <h2 className="text-xl font-semibold text-foreground mt-8 mb-4">Intended Use</h2>
            <p>Policy Chat is designed to help Rush employees quickly find and reference official Rush policies and procedures. It is a supplementary tool to assist with policy lookup.</p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-foreground mt-8 mb-4">AI-Generated Responses</h2>
            <p>Responses are generated using artificial intelligence and may not always be complete or accurate. Always verify important information by consulting the original policy document in PolicyTech or contacting the appropriate department.</p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-foreground mt-8 mb-4">Not a Substitute for Official Guidance</h2>
            <p>This tool does not replace official policy guidance, training, or consultation with supervisors and subject matter experts. For clinical decisions, always follow established protocols and consult appropriate personnel.</p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-foreground mt-8 mb-4">Confidentiality</h2>
            <p>Rush policies accessed through this application are internal documents. Do not share policy content with unauthorized individuals outside of Rush.</p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-foreground mt-8 mb-4">Emergency Situations</h2>
            <p>In an emergency, follow established emergency protocols and contact appropriate emergency services. Do not rely on this application for time-critical decisions.</p>
          </section>
        </div>

        <div className="mt-12 pt-8 border-t border-border">
          <Link href="/" className="text-rush-legacy hover:underline">
            &larr; Back to Policy Chat
          </Link>
        </div>
      </main>
    </div>
  );
}
