import DOMPurify from "isomorphic-dompurify";

/**
 * Sanitize content by stripping ALL HTML tags.
 * Used as defense-in-depth before React JSX rendering.
 */
export function sanitizeContent(text: string): string {
  if (!text) return "";
  return DOMPurify.sanitize(text, {
    ALLOWED_TAGS: [], // Strip ALL HTML tags — we render via react-markdown
    ALLOWED_ATTR: [],
  });
}
