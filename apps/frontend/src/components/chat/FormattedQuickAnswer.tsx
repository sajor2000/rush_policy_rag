"use client";

/**
 * Formatted text components for chat message display.
 *
 * This module contains React components for rendering formatted text
 * with citations, bold text, and policy references in chat messages.
 *
 * Extracted from ChatMessage.tsx as part of tech debt refactoring.
 */

import { cn } from "@/lib/utils";
import { Evidence } from "@/lib/api";
import { cleanQuickAnswerText } from "@/lib/chatMessageFormatting";

// ============================================================================
// FormattedTextSpan - Inline text formatting
// ============================================================================

export interface FormattedTextSpanProps {
  text: string;
  evidence?: Evidence[];
  onCitationClick?: (idx: number) => void;
}

/**
 * Format inline text with bold markers, citations, and policy references.
 *
 * Parses text for patterns:
 * - **bold text** - renders as bold with RUSH brand color
 * - [N] - renders as superscript citation link
 * - (Policy Title, Ref #XXX) - renders as inline citation with link
 * - Ref #XXX - renders as standalone reference badge
 *
 * @example
 * <FormattedTextSpan
 *   text="According to **Hand Hygiene** (Policy 528, Ref #528)..."
 *   evidence={evidence}
 *   onCitationClick={handleClick}
 * />
 */
export function FormattedTextSpan({
  text,
  evidence,
  onCitationClick,
}: FormattedTextSpanProps) {
  // Parse the text for bold markers, citation references, and policy refs
  const parts: React.ReactNode[] = [];
  let keyIdx = 0;

  // Pattern to match:
  // - **bold text**
  // - [N] citations
  // - (Policy Title, Ref #XXX) inline citations
  // - Ref #XXX standalone
  const pattern =
    /(\*\*([^*]+)\*\*|\[(\d+)\]|\(([^,]+),\s*Ref\s*#(\d+)\)|Ref\s*#(\d+))/g;
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
          title={
            hasEvidence ? `Jump to source ${match[3]}` : `Citation ${match[3]}`
          }
          disabled={!hasEvidence}
        >
          {match[3]}
        </button>
      );
    } else if (match[4] && match[5]) {
      // Inline citation (Policy Title, Ref #XXX)
      const refNum = match[5];
      const evidenceIdx =
        evidence?.findIndex((e) => e.reference_number === refNum) ?? -1;
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
      const evidenceIdx =
        evidence?.findIndex((e) => e.reference_number === refNum) ?? -1;
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

// ============================================================================
// FormattedQuickAnswer - Quick answer with list/paragraph formatting
// ============================================================================

export interface FormattedQuickAnswerProps {
  text: string;
  evidence?: Evidence[];
  onCitationClick?: (idx: number) => void;
}

/**
 * Parse quick answer text and render with formatted citations.
 *
 * Handles:
 * - Paragraph splitting
 * - Numbered list detection and formatting
 * - Bullet list detection and formatting
 * - Inline citations via FormattedTextSpan
 *
 * @example
 * <FormattedQuickAnswer
 *   text="According to policy:\n1. First step\n2. Second step"
 *   evidence={evidence}
 *   onCitationClick={handleClick}
 * />
 */
export function FormattedQuickAnswer({
  text,
  evidence,
  onCitationClick,
}: FormattedQuickAnswerProps) {
  if (!text) return null;

  // Clean the text first to remove redundant citation lists
  const cleanedText = cleanQuickAnswerText(text);

  // Split into paragraphs for better formatting
  const paragraphs = cleanedText.split(/\n\n+/).filter((p) => p.trim());

  return (
    <div className="space-y-3">
      {paragraphs.map((paragraph, pIdx) => {
        // Check if this paragraph is a list (numbered or bulleted)
        const lines = paragraph.split("\n").filter((l) => l.trim());
        const isNumberedList = lines.every((l) => /^\d+\.\s/.test(l.trim()));
        const isBulletList = lines.every((l) => /^[•\-\*]\s/.test(l.trim()));

        if (isNumberedList || isBulletList) {
          return (
            <ol
              key={pIdx}
              className={cn(
                "space-y-1.5 pl-4",
                isNumberedList ? "list-decimal" : "list-disc"
              )}
            >
              {lines.map((line, lIdx) => {
                const cleanLine = line
                  .trim()
                  .replace(/^(\d+\.|[•\-\*])\s*/, "");
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
              text={paragraph.replace(/\n/g, " ")}
              evidence={evidence}
              onCitationClick={onCitationClick}
            />
          </p>
        );
      })}
    </div>
  );
}
