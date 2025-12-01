"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { Copy, FileText, CheckCircle2, AlertCircle } from "lucide-react";
import { Evidence, Source } from "@/lib/api";

// All RUSH entity codes for checkbox display (matching PDF headers)
const RUSH_ENTITIES = ["RUMC", "RUMG", "ROPH", "RCMC", "RCH", "ROPPG", "RCMG", "RU"] as const;

interface ChatMessageProps {
  role: "user" | "assistant";
  content: string;
  summary?: string;
  evidence?: Evidence[];
  sources?: Source[];
  found?: boolean;
  onViewPdf?: (sourceFile: string, title: string) => void;
}

/** Parse applies_to string into set of entity codes */
function parseAppliesTo(appliesTo?: string): Set<string> {
  if (!appliesTo) return new Set();
  const normalized = appliesTo.toUpperCase().replace(/[☒☐]/g, "");
  const codes = normalized.split(/[\s,]+/).filter((c) => c.length > 0);
  return new Set(codes);
}

/** Maximum snippet length before truncation indicator is shown */
const MAX_SNIPPET_LENGTH = 2500;

/** Clean up snippet text - remove PDF header metadata and excessive whitespace */
function cleanSnippet(text: string): string {
  if (!text) return "";

  // Remove common PDF header patterns that are already shown in the card header
  let cleaned = text
    // Remove "Policy Title: ..." line
    .replace(/^Policy Title:.*$/gim, "")
    // Remove "Policy Number: ..." line
    .replace(/^Policy Number:.*$/gim, "")
    // Remove "Revised: ..." line
    .replace(/^Revised:.*$/gim, "")
    // Remove "Last Reviewed: ..." line
    .replace(/^Last Reviewed:.*$/gim, "")
    // Remove "Applies To: ..." line with checkboxes
    .replace(/^Applies To:.*$/gim, "")
    // Remove standalone checkbox lines (RUMC ☒ RUMG ☒ etc)
    .replace(/^[\s☒☐RUMCRUMGROPH RCMC RCH ROPPG RCMG RU]+$/gim, "")
    // Remove "Page X of Y" lines
    .replace(/^Page \d+ of \d+\s*$/gim, "")
    // Remove lines that are just dates (11/2024)
    .replace(/^\d{1,2}\/\d{4}\s*$/gim, "");

  // Clean up whitespace and format lists properly
  const lines = cleaned.split("\n").map((line) => line.trimEnd());
  const formattedLines: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue; // Skip empty lines

    // Detect bullet points and ensure consistent formatting
    if (/^[•\-\*]\s/.test(trimmed)) {
      formattedLines.push("• " + trimmed.replace(/^[•\-\*]\s*/, ""));
    }
    // Detect numbered lists (1., 2., a., b., etc.)
    else if (/^[0-9]+\.\s/.test(trimmed) || /^[a-z]\.\s/i.test(trimmed)) {
      formattedLines.push(trimmed);
    }
    // Regular text
    else {
      formattedLines.push(trimmed);
    }
  }

  return formattedLines
    .join("\n")
    .replace(/\n{3,}/g, "\n\n") // Max 2 consecutive newlines
    .trim();
}

/** Check if snippet was truncated (longer than display limit) */
function isSnippetTruncated(text: string): boolean {
  return text.length > MAX_SNIPPET_LENGTH;
}

/** Validate reference number format (e.g., "1.0.6", "ADM-001", etc.) */
function formatReferenceNumber(ref?: string): string {
  if (!ref || ref.trim() === "") return "N/A";
  // Clean and validate - only show if it looks like a valid ref number
  const cleaned = ref.trim();
  // Valid patterns: numbers with dots, alphanumeric with dashes
  if (/^[\w\d][\w\d.\-/]+$/.test(cleaned)) {
    return cleaned;
  }
  return "N/A";
}

/** Compact checkbox-style entity display */
function AppliesTo({ appliesTo }: { appliesTo?: string }) {
  const activeEntities = parseAppliesTo(appliesTo);
  return (
    <span className="inline-flex items-center gap-1 text-[9px]">
      {RUSH_ENTITIES.map((entity) => (
        <span key={entity} className="inline-flex items-center">
          <span
            className={cn(
              "inline-block w-2.5 h-2.5 border text-center leading-[10px] text-[7px] mr-0.5",
              activeEntities.has(entity)
                ? "border-rush-legacy bg-rush-legacy text-white"
                : "border-muted-foreground/30 bg-white"
            )}
          >
            {activeEntities.has(entity) ? "✓" : ""}
          </span>
          <span className="text-muted-foreground mr-1.5">{entity}</span>
        </span>
      ))}
    </span>
  );
}

export default function ChatMessage({
  role,
  content,
  summary,
  evidence,
  sources,
  found,
  onViewPdf,
}: ChatMessageProps) {
  const isUser = role === "user";
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const copiedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (copiedTimeoutRef.current) {
        clearTimeout(copiedTimeoutRef.current);
      }
    };
  }, []);

  const handleCopyCitation = async (idx: number, citation: string) => {
    if (!citation) return;
    try {
      await navigator.clipboard.writeText(citation);
      setCopiedIndex(idx);
      // Clear any existing timeout
      if (copiedTimeoutRef.current) {
        clearTimeout(copiedTimeoutRef.current);
      }
      copiedTimeoutRef.current = setTimeout(() => setCopiedIndex(null), 1600);
    } catch (error) {
      console.error("Failed to copy citation", error);
    }
  };

  const quickAnswer = summary ?? content;
  const evidenceCount = evidence?.length ?? 0;
  const hasAnswer = (found ?? true) && evidenceCount > 0;

  // Build sources from evidence if sources array is empty but evidence has source_file
  const effectiveSources = useMemo(() => {
    if (sources && sources.length > 0) return sources;
    if (!evidence || evidence.length === 0) return [];
    // Fallback: derive sources from evidence items that have source_file
    return evidence
      .filter((e) => e.source_file)
      .map((e) => ({
        citation: e.citation,
        source_file: e.source_file!,
        title: e.title,
        reference_number: e.reference_number,
        section: e.section,
        applies_to: e.applies_to,
        date_updated: e.date_updated,
      }));
  }, [sources, evidence]);

  return (
    <div
      className={cn("flex gap-4", isUser ? "justify-end" : "justify-start")}
      data-testid={`message-${role}`}
    >
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-3 shadow-sm",
          isUser ? "bg-rush-growth text-white" : "bg-rush-sage text-foreground"
        )}
      >
        {isUser ? (
          <p className="text-sm md:text-base">{content}</p>
        ) : !hasAnswer ? (
          <div className="rounded-2xl bg-white/95 border border-rush-legacy/20 shadow-[0_18px_40px_rgba(0,0,0,0.05)] p-5 space-y-3">
            <p className="text-sm md:text-base leading-relaxed text-foreground">
              {quickAnswer}
            </p>
            <p className="text-xs text-muted-foreground">
              Need assistance? Visit{" "}
              <a
                href="https://rushumc.navexone.com/"
                className="text-rush-legacy underline decoration-dotted underline-offset-4"
                target="_blank"
                rel="noreferrer"
              >
                rushumc.navexone.com
              </a>{" "}
              or contact Policy Administration.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {/* Quick Answer Section */}
            <div className="rounded-lg bg-white border border-rush-legacy/20 shadow-sm p-3">
              <p className="text-[10px] uppercase tracking-widest font-semibold text-rush-legacy mb-1">
                Quick Answer
              </p>
              <p className="text-sm leading-snug text-foreground">
                {quickAnswer}
              </p>
            </div>

            {/* Supporting Evidence Section - Compact PDF Style */}
            {evidence && evidence.length > 0 && (
            <div className="rounded-lg border border-rush-legacy/20 bg-white/90 p-3">
              {/* Section header with match type indicator */}
              {(() => {
                const hasRelated = evidence.some(e => e.match_type === "related");
                const allRelated = evidence.every(e => e.match_type === "related");
                return (
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-xs uppercase tracking-widest font-semibold text-gray-700">
                      {allRelated ? "Related Policies" : "Supporting Evidence"} ({evidence.length})
                    </p>
                    {hasRelated && !allRelated && (
                      <span className="inline-flex items-center gap-1 text-[10px] text-amber-700 bg-amber-50 px-2 py-0.5 rounded">
                        <AlertCircle className="h-3 w-3" />
                        Includes related content
                      </span>
                    )}
                  </div>
                );
              })()}
              {/* Note for all-related results */}
              {evidence.every(e => e.match_type === "related") && (
                <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5 mb-2">
                  The cited policy is not in our database. Showing related policies that may help.
                </p>
              )}
              <div className="space-y-2">
                {evidence.map((item, idx) => (
                  <div
                    key={`${item.citation}-${idx}`}
                    className={cn(
                      "border bg-white overflow-hidden",
                      item.match_type === "related"
                        ? "border-amber-300/50"
                        : "border-rush-legacy/30"
                    )}
                  >
                    {/* Compact Header */}
                    <div className={cn(
                      "border-b px-3 py-2",
                      item.match_type === "related"
                        ? "bg-amber-50/50 border-amber-200/50"
                        : "bg-rush-legacy/5 border-rush-legacy/20"
                    )}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            {/* Match type badge */}
                            {item.match_type === "verified" ? (
                              <span className="inline-flex items-center gap-0.5 text-[9px] text-rush-legacy bg-rush-sage/50 px-1.5 py-0.5 rounded font-medium">
                                <CheckCircle2 className="h-2.5 w-2.5" />
                                Cited
                              </span>
                            ) : item.match_type === "related" ? (
                              <span className="inline-flex items-center gap-0.5 text-[9px] text-amber-700 bg-amber-100 px-1.5 py-0.5 rounded font-medium">
                                <AlertCircle className="h-2.5 w-2.5" />
                                Related
                              </span>
                            ) : null}
                            <span className="text-xs font-semibold text-gray-900">
                              {item.title || "Policy"}
                            </span>
                            {item.reference_number && formatReferenceNumber(item.reference_number) !== "N/A" && (
                              <span className="text-xs text-gray-600">
                                Ref: {formatReferenceNumber(item.reference_number)}
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-2 mt-1 flex-wrap">
                            <AppliesTo appliesTo={item.applies_to} />
                            {item.date_updated && (
                              <span className="text-[10px] text-gray-500">
                                Updated: {item.date_updated}
                              </span>
                            )}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleCopyCitation(idx, item.citation)}
                          title="Copy citation to clipboard"
                          aria-label={copiedIndex === idx ? "Citation copied" : "Copy citation"}
                          className="flex-shrink-0 inline-flex items-center gap-1 text-xs text-rush-legacy hover:text-rush-legacy/80 transition-colors px-2 py-1 rounded border border-rush-legacy/30 bg-white font-medium"
                        >
                          <Copy className="h-3 w-3" />
                          {copiedIndex === idx ? "Copied!" : "Copy"}
                        </button>
                      </div>
                    </div>

                    {/* Policy Text - Cleaned */}
                    <div className="px-3 py-2 bg-white">
                      <div className="text-[13px] leading-relaxed text-gray-800 whitespace-pre-line">
                        {cleanSnippet(item.snippet)}
                      </div>
                      {isSnippetTruncated(item.snippet) && (
                        <p className="text-xs text-gray-500 italic mt-2 border-t border-gray-100 pt-2">
                          [Text excerpt — view full document for complete policy]
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
            )}

            {/* Source Documents - PDF Links at Bottom */}
            {effectiveSources.length > 0 && onViewPdf && (
              <div className="pt-2 border-t border-rush-legacy/15">
                <p className="text-[10px] uppercase tracking-widest text-muted-foreground mb-1.5 font-medium">
                  View Source PDFs
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {Array.from(
                    new Map(
                      effectiveSources
                        .filter((s) => s.source_file)
                        .map((s) => [s.source_file, s])
                    ).values()
                  ).map((source, idx) => (
                    <button
                      key={idx}
                      onClick={() => onViewPdf(source.source_file, source.title)}
                      className="inline-flex items-center gap-1 px-2 py-1 text-[11px] bg-white hover:bg-rush-legacy/5 border border-rush-legacy/25 rounded text-rush-legacy transition-colors"
                    >
                      <FileText className="h-3 w-3" />
                      <span className="max-w-[180px] truncate">
                        {source.title || source.source_file}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
