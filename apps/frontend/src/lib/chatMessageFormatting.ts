/**
 * Chat message formatting utilities for the RUSH Policy RAG frontend.
 *
 * This module contains pure utility functions for formatting and processing
 * chat message content. These functions have no React dependencies and can
 * be used throughout the application.
 *
 * Extracted from ChatMessage.tsx as part of tech debt refactoring.
 */

import type { Evidence } from "@/lib/api";

// ============================================================================
// Constants
// ============================================================================

/** All RUSH entity codes for checkbox display (matching PDF headers) */
export const RUSH_ENTITIES = [
  "RUMC",
  "RUMG",
  "RMG",
  "ROPH",
  "RCMC",
  "RCH",
  "ROPPG",
  "RCMG",
  "RU",
] as const;

/** Maximum snippet length before truncation indicator is shown */
export const MAX_SNIPPET_LENGTH = 2500;

// ============================================================================
// Entity Parsing
// ============================================================================

/**
 * Parse applies_to string into set of entity codes.
 *
 * Handles various formats from policy data:
 * - Comma-separated: "RUMC, RUMG, RMG"
 * - With checkboxes: "☒ RUMC ☒ RUMG ☐ RMG"
 * - Space-separated: "RUMC RUMG"
 *
 * @param appliesTo - Raw applies_to string from policy data
 * @returns Set of uppercase entity codes
 *
 * @example
 * parseAppliesTo("RUMC, RUMG")  // Set(["RUMC", "RUMG"])
 * parseAppliesTo("☒ RUMC ☐ RMG")  // Set(["RUMC"])
 */
export function parseAppliesTo(appliesTo?: string): Set<string> {
  if (!appliesTo) return new Set();
  const normalized = appliesTo.toUpperCase().replace(/[☒☐]/g, "");
  const codes = normalized.split(/[\s,]+/).filter((c) => c.length > 0);
  return new Set(codes);
}

// ============================================================================
// Snippet Cleaning
// ============================================================================

/**
 * Clean up snippet text by removing PDF header metadata and excessive whitespace.
 *
 * Removes redundant information that's already shown in card headers:
 * - Policy Title lines
 * - Policy Number lines
 * - Revised/Last Reviewed dates
 * - Applies To checkbox rows
 * - Page numbers
 *
 * @param text - Raw snippet text from policy chunk
 * @returns Cleaned text with proper list formatting
 *
 * @example
 * cleanSnippet("Policy Title: Hand Hygiene\nThe policy states...")
 * // "The policy states..."
 */
export function cleanSnippet(text: string): string {
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

/**
 * Check if snippet was truncated (longer than display limit).
 *
 * @param text - Snippet text to check
 * @returns True if text exceeds MAX_SNIPPET_LENGTH
 */
export function isSnippetTruncated(text: string): boolean {
  return text.length > MAX_SNIPPET_LENGTH;
}

// ============================================================================
// Reference Number Formatting
// ============================================================================

/**
 * Validate and format reference number for display.
 *
 * Valid patterns:
 * - Numbers with dots: "1.0.6", "528"
 * - Alphanumeric with dashes: "ADM-001", "HR-B-13.00"
 *
 * @param ref - Raw reference number string
 * @returns Formatted reference or "N/A" if invalid
 *
 * @example
 * formatReferenceNumber("528")  // "528"
 * formatReferenceNumber("")     // "N/A"
 * formatReferenceNumber(undefined)  // "N/A"
 */
export function formatReferenceNumber(ref?: string): string {
  if (!ref || ref.trim() === "") return "N/A";
  // Clean and validate - only show if it looks like a valid ref number
  const cleaned = ref.trim();
  // Valid patterns: numbers with dots, alphanumeric with dashes
  if (/^[\w\d][\w\d.\-/]+$/.test(cleaned)) {
    return cleaned;
  }
  return "N/A";
}

// ============================================================================
// Evidence ID Generation
// ============================================================================

/**
 * Generate a stable ID for evidence card based on title and ref.
 *
 * Used for scroll-to-evidence functionality and React keys.
 *
 * @param title - Policy title
 * @param refNum - Reference number
 * @param idx - Fallback index
 * @returns Stable ID string like "evidence-hand-hygiene-528"
 */
export function generateEvidenceId(
  title: string,
  refNum: string,
  idx: number
): string {
  const base = (title || "policy")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .slice(0, 30);
  return `evidence-${base}-${refNum || idx}`;
}

// ============================================================================
// Quick Answer Cleaning
// ============================================================================

/**
 * Clean the quick answer text by removing redundant citation lists.
 *
 * The LLM sometimes appends citation lists like:
 * "1. Ref #XXX — Title (Applies To: ...)"
 *
 * These are redundant because we show them in the Sources section.
 *
 * @param text - Raw quick answer text from LLM
 * @returns Cleaned text without redundant citations
 */
export function cleanQuickAnswerText(text: string): string {
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

// ============================================================================
// Citation Summary
// ============================================================================

/**
 * Build a formatted citation summary for the quick answer.
 *
 * Creates a string like:
 * "**Hand Hygiene** (Ref #528) [1], **Isolation** (Ref #369) [2]"
 *
 * @param evidence - Array of evidence items
 * @returns Formatted citation summary string (max 3 citations)
 */
export function buildCitationSummary(evidence?: Evidence[]): string {
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
