"use client";

import { useRef } from "react";
import RushLogo from "@/components/RushLogo";
import HeroSection from "@/components/HeroSection";
import PromptingTips from "@/components/PromptingTips";
import ChatInterface from "@/components/ChatInterface";

export default function Home() {
  const chatRef = useRef<HTMLDivElement>(null);

  const scrollToChat = () => {
    chatRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <div className="min-h-screen flex flex-col bg-background">
      <header className="sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="container max-w-6xl mx-auto px-4 h-16 flex items-center justify-between">
          <RushLogo data-testid="logo-header" />
        </div>
      </header>

      <main className="flex-1 flex flex-col">
        <HeroSection onGetStarted={scrollToChat} />
        
        <PromptingTips />

        <div ref={chatRef} className="flex-1 flex flex-col min-h-[600px] border-t border-border">
          <ChatInterface />
        </div>
      </main>

      <footer className="border-t border-border py-6 bg-card">
        <div className="container max-w-6xl mx-auto px-4">
          <p className="text-sm text-center text-muted-foreground">
            © 2025 Rush University System for Health. All rights reserved.
          </p>
        </div>
      </footer>
    </div>
  );
}
