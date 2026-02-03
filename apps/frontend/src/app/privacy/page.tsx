import { Metadata } from "next";
import Link from "next/link";
import RushLogo from "@/components/RushLogo";

export const metadata: Metadata = {
  title: "Privacy Policy | RUSH Policy Chat",
  description: "Privacy policy for RUSH Policy Chat application",
};

export default function PrivacyPolicy() {
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
        <h1 className="text-3xl font-semibold text-rush-legacy mb-8">Privacy Statement</h1>

        <div className="prose prose-rush max-w-none space-y-6 text-rush-charcoal">
          <p>This application is an internal tool for Rush University System for Health employees. Access requires authentication through Rush single sign-on (SSO).</p>

          <section>
            <h2 className="text-xl font-semibold text-foreground mt-8 mb-4">Information Collection</h2>
            <p>This application collects usage data to improve the service, including search queries and interaction patterns. All data is associated with your authenticated Rush account and is used solely for service improvement and support purposes.</p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-foreground mt-8 mb-4">Data Security</h2>
            <p>All data is stored securely within Rush&apos;s Azure cloud infrastructure and is subject to Rush&apos;s information security policies. Access is restricted to authenticated Rush employees.</p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-foreground mt-8 mb-4">Policy Content</h2>
            <p>This application provides access to official Rush policies and procedures. The policies displayed are internal documents and should be treated as confidential Rush information.</p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-foreground mt-8 mb-4">Questions</h2>
            <p>For questions about this application or data privacy, contact the Rush IT Help Desk or email <a href="mailto:rumcweb@rush.edu" className="text-rush-legacy hover:underline">rumcweb@rush.edu</a>.</p>
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
