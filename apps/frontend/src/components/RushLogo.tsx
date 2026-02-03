"use client";

export default function RushLogo({ className = "" }: { className?: string }) {
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <div className="relative h-10 w-[120px]">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/rush-logo.jpg"
          alt="Rush University System for Health"
          className="h-full w-full object-contain"
        />
      </div>
      <div className="h-8 w-px bg-border" />
      <span className="text-sm font-medium text-muted-foreground leading-tight">
        Policy Chat
      </span>
    </div>
  );
}
