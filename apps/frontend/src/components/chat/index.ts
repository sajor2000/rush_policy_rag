/**
 * Chat UI Components
 *
 * This module exports specialized components for the chat interface:
 *
 * - FormattedQuickAnswer: Renders formatted answer text with citations
 * - FormattedTextSpan: Inline text formatting with bold and citation links
 *
 * Usage:
 *   import { FormattedQuickAnswer, FormattedTextSpan } from "@/components/chat";
 *
 * These components were extracted from ChatMessage.tsx for better
 * maintainability and reusability.
 */

export {
  FormattedQuickAnswer,
  FormattedTextSpan,
  type FormattedTextSpanProps,
} from "./FormattedQuickAnswer";
