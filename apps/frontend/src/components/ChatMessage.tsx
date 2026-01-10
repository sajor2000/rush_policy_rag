"use client";

import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { cn } from "@/lib/utils";
import { Copy, FileText, CheckCircle2, AlertCircle, ExternalLink, BookOpen, Search, ChevronRight } from "lucide-react";
import { Evidence, Source } from "@/lib/api";
import { POLICYTECH_URL } from "@/lib/constants";
import {
  RUSH_ENTITIES,
  MAX_SNIPPET_LENGTH,
  parseAppliesTo,
  cleanSnippet,
  isSnippetTruncated,
  formatReferenceNumber,
  generateEvidenceId,
  buildCitationSummary,
} from "@/lib/chatMessageFormatting";
import {
  FormattedQuickAnswer,
  FormattedTextSpan,
} from "@/components/chat/FormattedQuickAnswer";

interface ChatMessageProps {
  role: "user" | "assistant";
  content: string;
  summary?: string;
  evidence?: Evidence[];
  sources?: Source[];
  found?: boolean;
  // For Deep Search results - enables "View PDF" button
  deepSearchPolicy?: {
    policyRef: string;
    policyTitle: string;
    sourceFile?: string;
  };
  onViewPdf?: (sourceFile: string, title: string, pageNumber?: number) => void;
  onSearchInPolicy?: (policyRef: string, policyTitle: string, sourceFile?: string) => void;
}

// FormattedQuickAnswer, FormattedTextSpan are now imported from @/components/chat/FormattedQuickAnswer
// buildCitationSummary and utilities are now imported from @/lib/chatMessageFormatting

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
  deepSearchPolicy,
  onViewPdf,
  onSearchInPolicy,
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
          "max-w-[85%] rounded-2xl px-4 py-3 shadow-sm overflow-hidden",
          isUser ? "bg-rush-growth text-white" : "bg-rush-sage text-foreground"
        )}
      >
        {isUser ? (
          <p className="text-sm md:text-base">{content}</p>
        ) : !hasAnswer ? (
          <div className="rounded-2xl bg-white/95 border border-rush-legacy/20 shadow-[0_18px_40px_rgba(0,0,0,0.05)] p-5 space-y-3">
            {/* Deep Search results: render with markdown-like formatting */}
            {deepSearchPolicy ? (
              <div className="space-y-3">
                {/* Parse and render content with basic markdown support */}
                <div className="text-sm leading-relaxed text-foreground whitespace-pre-wrap">
                  {quickAnswer.split('\n').map((line, lineIdx) => {
                    // Bold text: **text**
                    if (line.includes('**')) {
                      const parts = line.split(/(\*\*[^*]+\*\*)/g);
                      return (
                        <p key={lineIdx} className={line.startsWith('>') ? 'pl-3 border-l-2 border-rush-legacy/30 text-gray-600 my-2' : 'my-1'}>
                          {parts.map((part, partIdx) => {
                            if (part.startsWith('**') && part.endsWith('**')) {
                              return <strong key={partIdx} className="font-semibold text-rush-legacy">{part.slice(2, -2)}</strong>;
                            }
                            return <span key={partIdx}>{part}</span>;
                          })}
                        </p>
                      );
                    }
                    // Blockquote: > text
                    if (line.startsWith('>')) {
                      return (
                        <p key={lineIdx} className="pl-3 border-l-2 border-rush-legacy/30 text-gray-600 my-2 text-[13px]">
                          {line.slice(1).trim()}
                        </p>
                      );
                    }
                    // Italic: _text_
                    if (line.startsWith('_') && line.endsWith('_')) {
                      return (
                        <p key={lineIdx} className="text-xs text-gray-500 italic my-2">
                          {line.slice(1, -1)}
                        </p>
                      );
                    }
                    // Horizontal rule
                    if (line.startsWith('---')) {
                      return <hr key={lineIdx} className="my-3 border-rush-legacy/20" />;
                    }
                    // Empty lines
                    if (!line.trim()) {
                      return null;
                    }
                    // Regular text
                    return <p key={lineIdx} className="my-1">{line}</p>;
                  })}
                </div>

                {/* View PDF Button - only show if sourceFile available */}
                {deepSearchPolicy.sourceFile && onViewPdf && (
                  <div className="pt-3 border-t border-rush-legacy/20">
                    <button
                      onClick={() => onViewPdf(deepSearchPolicy.sourceFile!, deepSearchPolicy.policyTitle)}
                      className="inline-flex items-center gap-2 px-4 py-2 bg-rush-legacy hover:bg-rush-legacy/90 text-white rounded-lg text-sm font-medium transition-colors shadow-sm"
                    >
                      <FileText className="h-4 w-4" />
                      View PDF: {deepSearchPolicy.policyTitle}
                    </button>
                    <p className="text-xs text-gray-500 mt-2">
                      Use Ctrl+F in the PDF to search for the exact text shown above.
                    </p>
                  </div>
                )}
              </div>
            ) : (
              <>
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
              </>
            )}
          </div>
        ) : (
          <div className="space-y-3 overflow-hidden">
            {/* Quick Answer Section - Enhanced with citations */}
            <div className="rounded-lg bg-white border border-rush-legacy/20 shadow-sm p-3 overflow-hidden">
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
                    ).map((item, displayIdx) => {
                      const ref = formatReferenceNumber(item.reference_number);
                      return (
                        <button
                          key={item.idx}
                          onClick={() => scrollToEvidence(item.idx)}
                          className="flex items-center gap-3 p-2.5 bg-gradient-to-r from-white to-rush-sage/20 hover:to-rush-sage/40 border border-rush-legacy/20 rounded-lg transition-all duration-200 group text-left shadow-sm hover:shadow"
                        >
                          {/* Citation number badge */}
                          <span className="flex-shrink-0 inline-flex items-center justify-center w-6 h-6 text-xs font-bold bg-rush-legacy text-white rounded-md shadow-sm">
                            {displayIdx + 1}
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

            {/* Sticky Quick Access Panel - PDFs correlated with evidence */}
            {effectiveSources.length > 0 && onViewPdf && (
              <div className="sticky top-0 z-10 mt-3 p-3 bg-white/95 backdrop-blur-sm border border-rush-legacy/30 rounded-lg shadow-md">
                <p className="text-[10px] uppercase tracking-wider text-gray-600 mb-2 flex items-center gap-1.5">
                  <FileText className="h-3 w-3" />
                  Quick Access: Source PDFs
                </p>
                <div className="flex flex-wrap gap-2">
                  {effectiveSources.map((source, idx) => (
                    <button
                      key={idx}
                      onClick={() => onViewPdf(source.source_file, source.title)}
                      className="inline-flex items-center gap-1.5 text-xs bg-rush-legacy text-white hover:bg-rush-legacy/80 px-3 py-1.5 rounded-md transition-colors"
                    >
                      <span className="flex items-center justify-center w-5 h-5 bg-white text-rush-legacy rounded-full text-[10px] font-bold">
                        {idx + 1}
                      </span>
                      <span className="font-medium max-w-[200px] truncate">
                        {source.title || source.source_file}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Supporting Evidence Section - Compact PDF Style */}
            {evidence && evidence.length > 0 && (() => {
              // Build citation number map: first occurrence of each policy gets the display number
              // This ensures Policy Sources, Supporting Evidence, and PDFs all use same numbering
              const policyToCitationNum = new Map<string, number>();
              let citationCounter = 1;
              evidence.forEach((e) => {
                const key = e.reference_number || e.title;
                if (key && !policyToCitationNum.has(key)) {
                  policyToCitationNum.set(key, citationCounter++);
                }
              });

              return (
            <div className="rounded-lg border border-rush-legacy/20 bg-white/90 p-3 overflow-hidden">
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
              {/* Explanatory message about result limits */}
              <div className="mb-3 flex items-start gap-2 text-[11px] text-gray-600 bg-gray-50/50 border border-gray-200/50 rounded px-2.5 py-2">
                <div className="flex-shrink-0 mt-0.5">
                  <svg className="w-3.5 h-3.5 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                <div className="flex-1 min-w-0">
                  <p className="leading-relaxed">
                    We show you the <span className="font-medium text-gray-700">most relevant policies</span> that match your question. 
                    For comprehensive search of all policies, visit{" "}
                    <a
                      href={POLICYTECH_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-rush-legacy hover:text-rush-legacy/80 hover:underline font-medium"
                    >
                      PolicyTech
                      <ExternalLink className="h-3 w-3" />
                    </a>
                    .
                  </p>
                </div>
              </div>
              <div className="space-y-3">
                {evidence.map((item, idx) => {
                  // Get consistent citation number from policy map
                  const citationNum = policyToCitationNum.get(item.reference_number || item.title) || idx + 1;
                  return (
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
                            {/* Citation number badge - matches Policy Sources and PDFs */}
                            <span className="inline-flex items-center justify-center w-5 h-5 text-[10px] font-bold bg-rush-legacy text-white rounded">
                              {citationNum}
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
                            {item.page_number && (
                              <span className="text-[10px] text-rush-legacy bg-rush-sage/40 px-1.5 py-0.5 rounded font-medium">
                                Page {item.page_number}
                              </span>
                            )}
                            {item.date_updated && (
                              <span className="text-[10px] text-gray-500">
                                Updated: {item.date_updated}
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-1.5 flex-shrink-0">
                          {/* Search within policy button */}
                          {item.reference_number && onSearchInPolicy && (
                            <button
                              type="button"
                              onClick={() => onSearchInPolicy(
                                item.reference_number!,
                                item.title,
                                item.source_file
                              )}
                              title="Search within this policy"
                              aria-label="Search within policy"
                              className="inline-flex items-center gap-1 text-xs text-rush-legacy hover:text-rush-legacy/80 transition-colors px-2 py-1 rounded border border-rush-legacy/30 bg-white font-medium"
                            >
                              <Search className="h-3 w-3" />
                              Search
                            </button>
                          )}
                          {/* Copy citation button */}
                          <button
                            type="button"
                            onClick={() => handleCopyCitation(idx, item.citation)}
                            title="Copy citation to clipboard"
                            aria-label={copiedIndex === idx ? "Citation copied" : "Copy citation"}
                            className="inline-flex items-center gap-1 text-xs text-rush-legacy hover:text-rush-legacy/80 transition-colors px-2 py-1 rounded border border-rush-legacy/30 bg-white font-medium"
                          >
                            <Copy className="h-3 w-3" />
                            {copiedIndex === idx ? "Copied!" : "Copy"}
                          </button>
                          {/* View PDF button - directly correlated with this evidence */}
                          {item.source_file && onViewPdf && (
                            <button
                              type="button"
                              onClick={() => onViewPdf(item.source_file!, item.title, item.page_number)}
                              title="View source PDF"
                              aria-label="View source PDF"
                              className="inline-flex items-center gap-1 text-xs text-white bg-rush-legacy hover:bg-rush-legacy/80 transition-colors px-2 py-1 rounded font-medium"
                            >
                              <FileText className="h-3 w-3" />
                              PDF
                            </button>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Policy Text - Collapsible for "related" evidence */}
                    {item.match_type === "related" ? (
                      <details className="group">
                        <summary className="px-4 py-2 bg-amber-50/30 cursor-pointer hover:bg-amber-100/50 transition-colors text-sm text-gray-600 flex items-center gap-2">
                          <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90 text-amber-700" />
                          <span className="font-medium text-amber-800">Show related evidence</span>
                          <span className="text-xs text-gray-500">(may not directly support the answer)</span>
                        </summary>
                        <div className="px-4 py-3 bg-gradient-to-b from-white to-gray-50/50 overflow-hidden">
                          <div className="text-[13px] leading-[1.7] text-gray-700 whitespace-pre-line break-words [overflow-wrap:anywhere]">
                            {cleanSnippet(item.snippet)}
                          </div>
                          {isSnippetTruncated(item.snippet) && (
                            <p className="text-xs text-gray-400 italic mt-3 pt-2 border-t border-gray-100 flex items-center gap-1">
                              <span className="inline-block w-1 h-1 bg-gray-300 rounded-full"></span>
                              Text excerpt — view full document for complete policy
                            </p>
                          )}
                        </div>
                      </details>
                    ) : (
                      <div className="px-4 py-3 bg-gradient-to-b from-white to-gray-50/50 overflow-hidden">
                        <div className="text-[13px] leading-[1.7] text-gray-700 whitespace-pre-line break-words [overflow-wrap:anywhere]">
                          {cleanSnippet(item.snippet)}
                        </div>
                        {isSnippetTruncated(item.snippet) && (
                          <p className="text-xs text-gray-400 italic mt-3 pt-2 border-t border-gray-100 flex items-center gap-1">
                            <span className="inline-block w-1 h-1 bg-gray-300 rounded-full"></span>
                            Text excerpt — view full document for complete policy
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                  );
                })}
              </div>
              {/* PolicyTech search link */}
              <div className="mt-3 pt-3 border-t border-rush-legacy/10">
                <a
                  href={POLICYTECH_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-xs text-rush-legacy hover:text-rush-legacy/80 font-medium transition-colors"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  <span>Search all policies on PolicyTech</span>
                </a>
              </div>
            </div>
              );
            })()}

            {/* Source Documents - PDF Links at Bottom with matching citation numbers */}
            {effectiveSources.length > 0 && onViewPdf && (
              <div className="pt-2 border-t border-rush-legacy/15">
                <p className="text-[10px] uppercase tracking-widest text-muted-foreground mb-1.5 font-medium">
                  View Source PDFs
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {(() => {
                    // Build consistent citation number map by policy (reference_number or title)
                    // This matches the numbering in Policy Sources and Supporting Evidence sections
                    const policyToCitationNum = new Map<string, number>();
                    let citationCounter = 1;
                    evidence?.forEach((e) => {
                      const key = e.reference_number || e.title;
                      if (key && !policyToCitationNum.has(key)) {
                        policyToCitationNum.set(key, citationCounter++);
                      }
                    });

                    // Map source_file to citation number via its policy key
                    const sourceToNumber = new Map<string, number>();
                    evidence?.forEach((e) => {
                      if (e.source_file && !sourceToNumber.has(e.source_file)) {
                        const key = e.reference_number || e.title;
                        const num = policyToCitationNum.get(key) || 1;
                        sourceToNumber.set(e.source_file, num);
                      }
                    });

                    // Dedupe sources while preserving citation numbers
                    const uniqueSources = Array.from(
                      new Map(
                        effectiveSources
                          .filter((s) => s.source_file)
                          .map((s) => [s.source_file, s])
                      ).values()
                    );

                    return uniqueSources.map((source, idx) => {
                      const citationNum = sourceToNumber.get(source.source_file) || idx + 1;
                      return (
                        <button
                          key={idx}
                          onClick={() => onViewPdf(source.source_file, source.title)}
                          className="inline-flex items-center gap-1.5 px-2 py-1 text-[11px] bg-white hover:bg-rush-legacy/5 border border-rush-legacy/25 rounded text-rush-legacy transition-colors"
                        >
                          <FileText className="h-3 w-3" />
                          <span className="inline-flex items-center justify-center w-4 h-4 text-[9px] font-bold bg-rush-legacy text-white rounded">
                            {citationNum}
                          </span>
                          <span className="max-w-[180px] truncate">
                            {source.title || source.source_file}
                          </span>
                        </button>
                      );
                    });
                  })()}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
