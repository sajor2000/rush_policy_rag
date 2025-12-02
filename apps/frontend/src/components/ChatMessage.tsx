"use client";

import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { cn } from "@/lib/utils";
import { Copy, FileText, CheckCircle2, AlertCircle, ExternalLink, BookOpen } from "lucide-react";
import { Evidence, Source } from "@/lib/api";
import { POLICYTECH_URL } from "@/lib/constants";

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

/** Generate a stable ID for evidence card based on title and ref */
function generateEvidenceId(title: string, refNum: string, idx: number): string {
  const base = (title || "policy").toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 30);
  return `evidence-${base}-${refNum || idx}`;
}

/**
 * Clean the quick answer text by removing redundant citation lists
 * The LLM sometimes appends "1. Ref #XXX — Title..." which is redundant with our Sources section
 */
function cleanQuickAnswerText(text: string): string {
  if (!text) return "";

  // Remove numbered citation lists (e.g., "1. Ref #369 — Title (Applies To: ...)")
  // These are redundant because we show them in the Sources section
  let cleaned = text
    .replace(/\n*\d+\.\s*Ref\s*#\d+\s*[—–-]\s*[^\n]+(\([^)]*\))?/gi, "")
    .replace(/\n*Sources cited:?\s*\n*/gi, "")
    .trim();

  // Clean up multiple consecutive newlines
  cleaned = cleaned.replace(/\n{3,}/g, "\n\n");

  return cleaned;
}

/**
 * Parse quick answer text and render with formatted citations
 * Converts patterns like:
 * - **Policy Name** (Ref #XXX) → bold link to evidence
 * - [1] → superscript citation link
 * - Numbered lists and bullet points → proper formatting
 */
interface FormattedQuickAnswerProps {
  text: string;
  evidence?: Evidence[];
  onCitationClick?: (idx: number) => void;
}

function FormattedQuickAnswer({ text, evidence, onCitationClick }: FormattedQuickAnswerProps) {
  if (!text) return null;

  // Clean the text first to remove redundant citation lists
  const cleanedText = cleanQuickAnswerText(text);

  // Split into paragraphs for better formatting
  const paragraphs = cleanedText.split(/\n\n+/).filter(p => p.trim());

  return (
    <div className="space-y-3">
      {paragraphs.map((paragraph, pIdx) => {
        // Check if this paragraph is a list (numbered or bulleted)
        const lines = paragraph.split('\n').filter(l => l.trim());
        const isNumberedList = lines.every(l => /^\d+\.\s/.test(l.trim()));
        const isBulletList = lines.every(l => /^[•\-\*]\s/.test(l.trim()));

        if (isNumberedList || isBulletList) {
          return (
            <ol key={pIdx} className={cn(
              "space-y-1.5 pl-4",
              isNumberedList ? "list-decimal" : "list-disc"
            )}>
              {lines.map((line, lIdx) => {
                const cleanLine = line.trim().replace(/^(\d+\.|[•\-\*])\s*/, '');
                return (
                  <li key={lIdx} className="text-sm leading-relaxed">
                    <FormattedTextSpan
                      text={cleanLine}
                      evidence={evidence}
                      onCitationClick={onCitationClick}
                    />
                  </li>
                );
              })}
            </ol>
          );
        }

        // Regular paragraph
        return (
          <p key={pIdx} className="text-sm leading-relaxed">
            <FormattedTextSpan
              text={paragraph.replace(/\n/g, ' ')}
              evidence={evidence}
              onCitationClick={onCitationClick}
            />
          </p>
        );
      })}
    </div>
  );
}

/** Format inline text with bold, citations, and policy references */
function FormattedTextSpan({
  text,
  evidence,
  onCitationClick
}: {
  text: string;
  evidence?: Evidence[];
  onCitationClick?: (idx: number) => void;
}) {
  // Parse the text for bold markers, citation references, and policy refs
  const parts: React.ReactNode[] = [];
  let keyIdx = 0;

  // Pattern to match:
  // - **bold text**
  // - [N] citations
  // - (Policy Title, Ref #XXX) inline citations
  // - Ref #XXX standalone
  const pattern = /(\*\*([^*]+)\*\*|\[(\d+)\]|\(([^,]+),\s*Ref\s*#(\d+)\)|Ref\s*#(\d+))/g;
  let lastIndex = 0;
  let match;

  while ((match = pattern.exec(text)) !== null) {
    // Add text before the match
    if (match.index > lastIndex) {
      parts.push(
        <span key={keyIdx++}>{text.slice(lastIndex, match.index)}</span>
      );
    }

    if (match[2]) {
      // Bold text (**text**)
      parts.push(
        <strong key={keyIdx++} className="font-semibold text-rush-legacy">
          {match[2]}
        </strong>
      );
    } else if (match[3]) {
      // Citation reference [N]
      const citIdx = parseInt(match[3], 10) - 1;
      const hasEvidence = evidence && citIdx >= 0 && citIdx < evidence.length;
      parts.push(
        <button
          key={keyIdx++}
          onClick={() => hasEvidence && onCitationClick?.(citIdx)}
          className={cn(
            "inline-flex items-center justify-center",
            "text-[10px] font-bold min-w-[16px] h-4 px-1",
            "rounded-sm align-super -translate-y-0.5",
            hasEvidence
              ? "bg-rush-legacy text-white hover:bg-rush-legacy/80 cursor-pointer"
              : "bg-gray-300 text-gray-600 cursor-default"
          )}
          title={hasEvidence ? `Jump to source ${match[3]}` : `Citation ${match[3]}`}
          disabled={!hasEvidence}
        >
          {match[3]}
        </button>
      );
    } else if (match[4] && match[5]) {
      // Inline citation (Policy Title, Ref #XXX)
      const refNum = match[5];
      const evidenceIdx = evidence?.findIndex(e => e.reference_number === refNum) ?? -1;
      const hasEvidence = evidenceIdx >= 0;
      parts.push(
        <span key={keyIdx++} className="inline-flex items-center gap-1">
          <span className="text-gray-500">(</span>
          <button
            onClick={() => hasEvidence && onCitationClick?.(evidenceIdx)}
            className={cn(
              "font-medium",
              hasEvidence
                ? "text-rush-legacy hover:underline cursor-pointer"
                : "text-gray-700"
            )}
          >
            {match[4].trim()}
          </button>
          <span className="text-xs bg-rush-sage/40 px-1 py-0.5 rounded text-rush-legacy font-medium">
            #{refNum}
          </span>
          <span className="text-gray-500">)</span>
        </span>
      );
    } else if (match[6]) {
      // Standalone Ref #XXX
      const refNum = match[6];
      const evidenceIdx = evidence?.findIndex(e => e.reference_number === refNum) ?? -1;
      const hasEvidence = evidenceIdx >= 0;
      parts.push(
        <button
          key={keyIdx++}
          onClick={() => hasEvidence && onCitationClick?.(evidenceIdx)}
          className={cn(
            "inline-flex items-center text-xs bg-rush-sage/40 px-1.5 py-0.5 rounded font-medium",
            hasEvidence
              ? "text-rush-legacy hover:bg-rush-sage/60 cursor-pointer"
              : "text-gray-600"
          )}
        >
          Ref #{refNum}
        </button>
      );
    }

    lastIndex = match.index + match[0].length;
  }

  // Add any remaining text
  if (lastIndex < text.length) {
    parts.push(<span key={keyIdx++}>{text.slice(lastIndex)}</span>);
  }

  return <>{parts}</>;
}

/** Build a formatted citation summary for the quick answer */
function buildCitationSummary(evidence?: Evidence[]): string {
  if (!evidence || evidence.length === 0) return "";

  const uniquePolicies = new Map<string, Evidence>();
  for (const e of evidence) {
    const key = e.reference_number || e.title;
    if (key && !uniquePolicies.has(key)) {
      uniquePolicies.set(key, e);
    }
  }

  const citations = Array.from(uniquePolicies.values())
    .slice(0, 3)
    .map((e, idx) => {
      const ref = formatReferenceNumber(e.reference_number);
      if (ref !== "N/A") {
        return `**${e.title}** (Ref #${ref}) [${idx + 1}]`;
      }
      return `**${e.title}** [${idx + 1}]`;
    });

  return citations.join(", ");
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
  const [highlightedEvidence, setHighlightedEvidence] = useState<number | null>(null);
  const copiedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const evidenceRefs = useRef<Map<number, HTMLDivElement>>(new Map());

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (copiedTimeoutRef.current) {
        clearTimeout(copiedTimeoutRef.current);
      }
    };
  }, []);

  // Scroll to and highlight an evidence card
  const scrollToEvidence = useCallback((idx: number) => {
    const element = evidenceRefs.current.get(idx);
    if (element) {
      element.scrollIntoView({ behavior: "smooth", block: "center" });
      setHighlightedEvidence(idx);
      // Clear highlight after animation
      setTimeout(() => setHighlightedEvidence(null), 2000);
    }
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

  // Detect refusal responses - should NOT show any evidence/sources
  const isRefusalResponse = useMemo(() => {
    const text = (quickAnswer || "").toLowerCase();
    const refusalPatterns = [
      "i only answer rush policy",
      "only answer rush policy questions",
      "i could not find",
      "could not find this in rush",
      "outside my scope",
      "outside the scope",
      "cannot provide guidance",
      "please rephrase",
      "clarify your question",
    ];
    return refusalPatterns.some(pattern => text.includes(pattern));
  }, [quickAnswer]);

  const hasAnswer = (found ?? true) && evidenceCount > 0 && !isRefusalResponse;

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
                href={POLICYTECH_URL}
                className="text-rush-legacy underline decoration-dotted underline-offset-4"
                target="_blank"
                rel="noreferrer"
              >
                {POLICYTECH_URL.replace(/^https?:\/\//, "").replace(/\/$/, "")}
              </a>{" "}
              or contact Policy Administration.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {/* Quick Answer Section - Enhanced with citations */}
            <div className="rounded-lg bg-white border border-rush-legacy/20 shadow-sm p-3">
              <div className="flex items-center gap-2 mb-2">
                <BookOpen className="h-4 w-4 text-rush-legacy" />
                <p className="text-[10px] uppercase tracking-widest font-semibold text-rush-legacy">
                  Quick Answer
                </p>
              </div>
              <div className="text-sm leading-relaxed text-foreground">
                <FormattedQuickAnswer
                  text={quickAnswer}
                  evidence={evidence}
                  onCitationClick={scrollToEvidence}
                />
              </div>

              {/* Citation Sources Summary - Card Style */}
              {evidence && evidence.length > 0 && (
                <div className="mt-4 pt-3 border-t border-rush-legacy/10">
                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2 font-semibold flex items-center gap-1.5">
                    <FileText className="h-3 w-3" />
                    Policy Sources
                  </p>
                  <div className="grid gap-2">
                    {Array.from(
                      new Map(
                        evidence.map((e, idx) => [e.reference_number || e.title, { ...e, idx }])
                      ).values()
                    ).slice(0, 4).map((item) => {
                      const ref = formatReferenceNumber(item.reference_number);
                      return (
                        <button
                          key={item.idx}
                          onClick={() => scrollToEvidence(item.idx)}
                          className="flex items-center gap-3 p-2.5 bg-gradient-to-r from-white to-rush-sage/20 hover:to-rush-sage/40 border border-rush-legacy/20 rounded-lg transition-all duration-200 group text-left shadow-sm hover:shadow"
                        >
                          {/* Citation number badge */}
                          <span className="flex-shrink-0 inline-flex items-center justify-center w-6 h-6 text-xs font-bold bg-rush-legacy text-white rounded-md shadow-sm">
                            {item.idx + 1}
                          </span>

                          {/* Policy info */}
                          <div className="flex-1 min-w-0">
                            <p className="font-medium text-sm text-gray-900 group-hover:text-rush-legacy truncate">
                              {item.title}
                            </p>
                            {ref !== "N/A" && (
                              <p className="text-xs text-gray-500 mt-0.5">
                                Reference #{ref}
                              </p>
                            )}
                          </div>

                          {/* Chevron indicator */}
                          <span className="flex-shrink-0 text-rush-legacy/50 group-hover:text-rush-legacy transition-colors">
                            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                            </svg>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
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
              <div className="space-y-3">
                {evidence.map((item, idx) => (
                  <div
                    key={`${item.citation}-${idx}`}
                    ref={(el) => {
                      if (el) evidenceRefs.current.set(idx, el);
                    }}
                    id={generateEvidenceId(item.title, item.reference_number || "", idx)}
                    className={cn(
                      "border bg-white overflow-hidden transition-all duration-300 rounded-lg shadow-sm",
                      item.match_type === "related"
                        ? "border-amber-300/50"
                        : "border-rush-legacy/20",
                      highlightedEvidence === idx && "ring-2 ring-rush-legacy ring-offset-2 shadow-lg"
                    )}
                  >
                    {/* Compact Header with Citation Number */}
                    <div className={cn(
                      "border-b px-3 py-2",
                      item.match_type === "related"
                        ? "bg-amber-50/50 border-amber-200/50"
                        : "bg-rush-legacy/5 border-rush-legacy/20"
                    )}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            {/* Citation number badge */}
                            <span className="inline-flex items-center justify-center w-5 h-5 text-[10px] font-bold bg-rush-legacy text-white rounded">
                              {idx + 1}
                            </span>
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
                              <span className="text-xs font-medium text-rush-legacy bg-rush-sage/30 px-1.5 py-0.5 rounded">
                                Ref #{formatReferenceNumber(item.reference_number)}
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

                    {/* Policy Text - Cleaned with better formatting */}
                    <div className="px-4 py-3 bg-gradient-to-b from-white to-gray-50/50">
                      <div className="text-[13px] leading-[1.7] text-gray-700 whitespace-pre-line prose prose-sm prose-gray max-w-none">
                        {cleanSnippet(item.snippet)}
                      </div>
                      {isSnippetTruncated(item.snippet) && (
                        <p className="text-xs text-gray-400 italic mt-3 pt-2 border-t border-gray-100 flex items-center gap-1">
                          <span className="inline-block w-1 h-1 bg-gray-300 rounded-full"></span>
                          Text excerpt — view full document for complete policy
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
