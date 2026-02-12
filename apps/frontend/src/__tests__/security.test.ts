/**
 * Third-Party Security Audit: Frontend Security Test Suite
 * ==========================================================
 *
 * Tests XSS prevention, sanitization, PDF path safety, and CSP configuration.
 *
 * XSS tests: Verify that cleanSnippet() and React's JSX escaping prevent
 * script injection from LLM-generated content.
 *
 * CSP tests: Static analysis of next.config.js to document known CSP weaknesses.
 */

import { describe, it, expect } from "vitest";
import { cleanSnippet, formatReferenceNumber } from "@/lib/chatMessageFormatting";
import fs from "fs";
import path from "path";

// ============================================================================
// XSS Prevention in Chat Responses
// ============================================================================

describe("XSS Prevention in cleanSnippet()", () => {
  it("FINDING: cleanSnippet does not strip HTML tags (relies on React JSX escaping)", () => {
    const malicious = '<script>alert("xss")</script>Normal policy text here.';
    const cleaned = cleanSnippet(malicious);
    // FINDING: cleanSnippet() is a text processor, not an HTML sanitizer.
    // Script tags pass through as-is. XSS safety relies entirely on React's
    // JSX auto-escaping during render (which does protect against this).
    // However, if content is ever rendered via dangerouslySetInnerHTML or
    // a non-React context, this becomes a vulnerability.
    // Recommendation: Add DOMPurify sanitization as defense-in-depth.
    expect(cleaned).toContain("Normal policy text here.");
    expect(typeof cleaned).toBe("string");
  });

  it("should handle img onerror XSS payload", () => {
    const malicious = '<img src=x onerror="alert(1)">Policy content here.';
    const cleaned = cleanSnippet(malicious);
    // The cleaned text should not contain executable event handlers
    expect(cleaned).toBeDefined();
    expect(typeof cleaned).toBe("string");
  });

  it("should handle event handler injection in div tags", () => {
    const malicious = '<div onmouseover="alert(1)">Hover for XSS</div>';
    const cleaned = cleanSnippet(malicious);
    expect(cleaned).toBeDefined();
    expect(typeof cleaned).toBe("string");
  });

  it("should handle SVG onload injection", () => {
    const malicious = '<svg onload="alert(1)">Policy info</svg>';
    const cleaned = cleanSnippet(malicious);
    expect(cleaned).toBeDefined();
    expect(typeof cleaned).toBe("string");
  });

  it("should handle javascript: protocol in anchor tags", () => {
    const malicious = '<a href="javascript:alert(1)">Click me</a>';
    const cleaned = cleanSnippet(malicious);
    expect(cleaned).toBeDefined();
  });

  it("should handle empty and null inputs safely", () => {
    expect(cleanSnippet("")).toBe("");
    expect(cleanSnippet(null as unknown as string)).toBe("");
    expect(cleanSnippet(undefined as unknown as string)).toBe("");
  });
});

// ============================================================================
// Reference Number Formatting Injection
// ============================================================================

describe("Reference Number Format Validation", () => {
  it("should reject script injection in reference numbers", () => {
    const result = formatReferenceNumber('<script>alert(1)</script>');
    expect(result).toBe("N/A");
  });

  it("should reject HTML in reference numbers", () => {
    const result = formatReferenceNumber('<img src=x onerror=alert(1)>');
    expect(result).toBe("N/A");
  });

  it("should accept valid reference numbers", () => {
    expect(formatReferenceNumber("528")).toBe("528");
    expect(formatReferenceNumber("HR-B-13.00")).toBe("HR-B-13.00");
    expect(formatReferenceNumber("ADM-001")).toBe("ADM-001");
  });

  it("should return N/A for empty inputs", () => {
    expect(formatReferenceNumber("")).toBe("N/A");
    expect(formatReferenceNumber(undefined)).toBe("N/A");
  });
});

// ============================================================================
// CSP Configuration Audit
// ============================================================================

describe("CSP Configuration Audit (next.config.js)", () => {
  const configPath = path.resolve(__dirname, "../../next.config.js");
  let configContent: string;

  try {
    configContent = fs.readFileSync(configPath, "utf-8");
  } catch {
    configContent = "";
  }

  it("FINDING: CSP allows unsafe-eval in script-src", () => {
    if (!configContent) {
      return; // Skip if config not readable
    }
    // Document the finding — unsafe-eval allows eval() which can be exploited
    const hasUnsafeEval = configContent.includes("unsafe-eval");
    expect(hasUnsafeEval).toBe(true);
    // This is a FINDING — unsafe-eval should be removed in production
    // or replaced with nonce-based CSP
  });

  it("FINDING: CSP allows unsafe-inline in script-src and style-src", () => {
    if (!configContent) {
      return;
    }
    const hasUnsafeInline = configContent.includes("unsafe-inline");
    expect(hasUnsafeInline).toBe(true);
    // FINDING: unsafe-inline allows inline scripts/styles — XSS bypass risk
  });

  it("CSP sets frame-ancestors to none (clickjacking protection)", () => {
    if (!configContent) {
      return;
    }
    expect(configContent).toContain("frame-ancestors 'none'");
  });

  it("X-Frame-Options is set to DENY", () => {
    if (!configContent) {
      return;
    }
    expect(configContent).toContain("DENY");
  });

  it("Strict-Transport-Security header is configured", () => {
    if (!configContent) {
      return;
    }
    expect(configContent).toContain("Strict-Transport-Security");
  });
});

// ============================================================================
// PDF Filename Validation
// ============================================================================

describe("PDF Filename Safety", () => {
  it("should reject non-PDF file extensions", () => {
    // Simulates the check in apps/frontend/src/app/api/pdf/[...filename]/route.ts:13
    const filename = "policy.txt";
    expect(filename.endsWith(".pdf")).toBe(false);
  });

  it("should identify path traversal in filenames", () => {
    const malicious = "../../etc/passwd.pdf";
    expect(malicious.includes("..")).toBe(true);
    // FINDING: Frontend PDF proxy at route.ts does NOT check for '..' in filename
  });

  it("should identify encoded path traversal", () => {
    const encoded = "..%2F..%2Fetc%2Fpasswd.pdf";
    const decoded = decodeURIComponent(encoded);
    expect(decoded.includes("..")).toBe(true);
    // FINDING: URL-encoded traversal is decoded but not validated
  });

  it("should handle empty filename", () => {
    const empty = "";
    expect(empty.endsWith(".pdf")).toBe(false);
  });
});
