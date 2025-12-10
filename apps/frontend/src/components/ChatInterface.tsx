"use client";

import { useState, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Send, Sparkles, Loader2, Search, HelpCircle, X } from "lucide-react";
import ChatMessage from "./ChatMessage";
import LoadingState from "./LoadingState";
import ErrorMessage from "./ErrorMessage";
import PDFViewer from "./PDFViewer";
import InstanceSearchModal from "./InstanceSearchModal";
import { sendMessage, searchInstances, type Source, type Evidence, type InstanceSearchResponse } from "@/lib/api";

interface Message {
  role: "user" | "assistant";
  content: string;
  summary?: string;
  evidence?: Evidence[];
  sources?: Source[];
  rawResponse?: string;
  found?: boolean;
}

export default function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // PDF Viewer state
  const [pdfViewerOpen, setPdfViewerOpen] = useState(false);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfTitle, setPdfTitle] = useState<string>("");
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [lastPdfSourceFile, setLastPdfSourceFile] = useState<string>("");
  const [pdfInitialPage, setPdfInitialPage] = useState(1);

  // Instance Search Modal state
  const [instanceSearchOpen, setInstanceSearchOpen] = useState(false);
  const [instanceSearchPolicy, setInstanceSearchPolicy] = useState<{
    policyRef: string;
    policyTitle: string;
    sourceFile?: string;
  } | null>(null);

  // Deep Search Mode state - allows searching within a specific policy
  const [deepSearchMode, setDeepSearchMode] = useState(false);
  const [deepSearchPolicyRef, setDeepSearchPolicyRef] = useState("");
  const [showDeepSearchHelp, setShowDeepSearchHelp] = useState(false);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleViewPdf = async (sourceFile: string, title: string) => {
    // Store source file for retry functionality
    setLastPdfSourceFile(sourceFile);

    // Reset state and open viewer with loading
    setPdfUrl(null);
    setPdfError(null);
    setPdfLoading(true);
    setPdfTitle(title);
    setPdfViewerOpen(true);
    setPdfInitialPage(1); // Reset to page 1 for normal PDF viewing

    try {
      const response = await fetch(`/api/pdf/${encodeURIComponent(sourceFile)}`);

      // Parse response JSON safely
      let data: Record<string, unknown>;
      try {
        data = await response.json();
      } catch {
        throw new Error(`Failed to load PDF (invalid response)`);
      }

      if (!response.ok) {
        const errorMessage =
          (typeof data.error === 'string' ? data.error : null) ||
          (typeof data.detail === 'string' ? data.detail : null) ||
          `Failed to load PDF (${response.status})`;
        throw new Error(errorMessage);
      }

      // Validate URL field
      if (!data.url || typeof data.url !== 'string') {
        throw new Error("Invalid PDF URL response from server");
      }

      setPdfUrl(data.url);
    } catch (err) {
      console.error("Error fetching PDF URL:", err);
      const errorMessage = err instanceof Error ? err.message : "Failed to load PDF";
      setPdfError(errorMessage);
      setPdfUrl(null);
    } finally {
      setPdfLoading(false);
    }
  };

  const handleClosePdf = () => {
    setPdfViewerOpen(false);
    setPdfUrl(null);
    setPdfTitle("");
    setPdfError(null);
  };

  const handleRetryPdf = () => {
    if (lastPdfSourceFile) {
      handleViewPdf(lastPdfSourceFile, pdfTitle);
    }
  };

  // Handler for "Search" button on evidence cards - opens InstanceSearchModal
  const handleSearchInPolicy = (policyRef: string, policyTitle: string, sourceFile?: string) => {
    setInstanceSearchPolicy({ policyRef, policyTitle, sourceFile });
    setInstanceSearchOpen(true);
  };

  // Handler for Deep Search Mode submissions
  const handleDeepSearch = async (searchTerm: string) => {
    if (!deepSearchPolicyRef.trim()) {
      setError("Please enter a policy reference number (e.g., 528, 1515)");
      return;
    }

    setIsLoading(true);
    setError(null);
    setMessages((prev) => [...prev, {
      role: "user",
      content: `[Deep Search in Ref #${deepSearchPolicyRef}] ${searchTerm}`
    }]);

    try {
      const result = await searchInstances(deepSearchPolicyRef.trim(), searchTerm);

      // Format the results as a message
      let responseContent = "";
      if (result.total_instances === 0) {
        responseContent = `No results found for "${searchTerm}" in policy Ref #${deepSearchPolicyRef}.\n\nTry different search terms or check if the policy reference number is correct.`;
      } else {
        responseContent = `Found **${result.total_instances} result${result.total_instances !== 1 ? 's' : ''}** for "${searchTerm}" in **${result.policy_title}** (Ref #${result.policy_ref}):\n\n`;

        result.instances.slice(0, 10).forEach((instance, idx) => {
          const pageInfo = instance.page_number ? `Page ${instance.page_number}` : "N/A";
          const sectionInfo = instance.section ? `, Section ${instance.section}` : "";
          responseContent += `**${idx + 1}. ${pageInfo}${sectionInfo}**\n`;
          responseContent += `"...${instance.context.slice(0, 200)}${instance.context.length > 200 ? '...' : ''}"\n\n`;
        });

        if (result.total_instances > 10) {
          responseContent += `\n_Showing first 10 of ${result.total_instances} results. Click "Search" on an evidence card to see all._`;
        }
      }

      setMessages((prev) => [...prev, {
        role: "assistant",
        content: responseContent,
        found: result.total_instances > 0
      }]);

      // If results found and source_file available, store for potential PDF viewing
      if (result.source_file) {
        setInstanceSearchPolicy({
          policyRef: result.policy_ref,
          policyTitle: result.policy_title,
          sourceFile: result.source_file
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Deep search failed");
    } finally {
      setIsLoading(false);
    }
  };

  // Handler for navigating to a specific page in PDF from instance search results
  const handleNavigateToPage = async (pageNumber: number, sourceFile?: string) => {
    // Close the instance search modal
    setInstanceSearchOpen(false);

    // Use the source file from the search, or fall back to lastPdfSourceFile
    const fileToOpen = sourceFile || instanceSearchPolicy?.sourceFile || lastPdfSourceFile;
    const titleToUse = instanceSearchPolicy?.policyTitle || pdfTitle;

    if (!fileToOpen) {
      console.error("No source file available to open PDF");
      return;
    }

    // Set the initial page before opening
    setPdfInitialPage(pageNumber);

    // Reset PDF state and open viewer with loading
    setPdfUrl(null);
    setPdfError(null);
    setPdfLoading(true);
    setPdfTitle(titleToUse);
    setPdfViewerOpen(true);
    setLastPdfSourceFile(fileToOpen);

    try {
      const response = await fetch(`/api/pdf/${encodeURIComponent(fileToOpen)}`);

      let data: Record<string, unknown>;
      try {
        data = await response.json();
      } catch {
        throw new Error(`Failed to load PDF (invalid response)`);
      }

      if (!response.ok) {
        const errorMessage =
          (typeof data.error === 'string' ? data.error : null) ||
          (typeof data.detail === 'string' ? data.detail : null) ||
          `Failed to load PDF (${response.status})`;
        throw new Error(errorMessage);
      }

      if (!data.url || typeof data.url !== 'string') {
        throw new Error("Invalid PDF URL response from server");
      }

      setPdfUrl(data.url);
    } catch (err) {
      console.error("Error fetching PDF URL:", err);
      const errorMessage = err instanceof Error ? err.message : "Failed to load PDF";
      setPdfError(errorMessage);
      setPdfUrl(null);
    } finally {
      setPdfLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!input.trim() || isLoading) return;

    const userMessage = input.trim();
    setInput("");
    setError(null);

    // If Deep Search mode is enabled, use the instance search API
    if (deepSearchMode) {
      await handleDeepSearch(userMessage);
      return;
    }

    // Normal Q&A mode
    setMessages((prev) => [...prev, { role: "user", content: userMessage }]);
    setIsLoading(true);

    try {
      const result = await sendMessage(userMessage);

      setMessages((prev) => [...prev, {
        role: "assistant",
        content: result.summary || result.response,
        summary: result.summary || result.response,
        evidence: result.evidence || [],
        sources: result.sources || [],
        rawResponse: result.raw_response,
        found: result.found !== undefined ? result.found : (result.evidence?.length ?? 0) > 0
      }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsLoading(false);
    }
  };

  const handleRetry = () => {
    setError(null);
    if (messages.length > 0 && messages[messages.length - 1].role === "user") {
      const lastMessage = messages[messages.length - 1].content;
      setMessages((prev) => prev.slice(0, -1));
      setInput(lastMessage);
      textareaRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  // Direct click handler for the submit button (backup for form submission)
  // Also reads directly from the textarea ref in case React state wasn't updated
  const handleButtonClick = () => {
    // Try to get value from ref first (handles browser automation edge cases)
    const textareaValue = textareaRef.current?.value || input;
    if (textareaValue.trim() && !isLoading) {
      // Update React state if it's out of sync with DOM
      if (textareaValue !== input) {
        setInput(textareaValue);
      }
      // Use the textarea value directly for submission
      const userMessage = textareaValue.trim();
      setInput("");
      setError(null);

      // If Deep Search mode is enabled, use the instance search API
      if (deepSearchMode) {
        handleDeepSearch(userMessage);
        return;
      }

      // Normal Q&A mode
      setMessages((prev) => [...prev, { role: "user", content: userMessage }]);
      setIsLoading(true);

      sendMessage(userMessage)
        .then((result) => {
          setMessages((prev) => [...prev, {
            role: "assistant",
            content: result.summary || result.response,
            summary: result.summary || result.response,
            evidence: result.evidence || [],
            sources: result.sources || [],
            rawResponse: result.raw_response,
            found: result.found !== undefined ? result.found : (result.evidence?.length ?? 0) > 0
          }]);
        })
        .catch((err) => {
          setError(err instanceof Error ? err.message : "An error occurred");
        })
        .finally(() => {
          setIsLoading(false);
        });
    }
  };

  return (
    <section className="w-full flex-1 flex flex-col">
      <div className="container max-w-4xl mx-auto px-4 flex flex-col h-full">
        {messages.length === 0 ? (
          <div className="flex-1 flex items-center justify-center py-12">
            <div className="text-center space-y-4 max-w-md">
              <div className="w-16 h-16 mx-auto rounded-full bg-rush-sage flex items-center justify-center">
                <Sparkles className="h-8 w-8 text-rush-legacy" />
              </div>
              <h3 className="text-xl font-semibold text-foreground">
                We're here to help. Quick Answer + Evidence, every time
              </h3>
              <p className="text-muted-foreground">
                Ask any RUSH policy question and you&apos;ll get a concise summary
                plus the verbatim sections that prove it.
              </p>
            </div>
          </div>
        ) : (
          <div
            className="flex-1 overflow-y-auto py-6 space-y-6"
            role="log"
            aria-live="polite"
            aria-label="Chat messages"
          >
            {messages.map((message, index) => (
              <ChatMessage
                key={index}
                {...message}
                onViewPdf={handleViewPdf}
                onSearchInPolicy={handleSearchInPolicy}
              />
            ))}
            {isLoading && (
              <div aria-live="assertive" aria-busy="true">
                <LoadingState />
              </div>
            )}
            {error && (
              <div role="alert" aria-live="assertive">
                <ErrorMessage message={error} onRetry={handleRetry} />
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* PDF Viewer Modal */}
        <PDFViewer
          isOpen={pdfViewerOpen}
          onClose={handleClosePdf}
          pdfUrl={pdfUrl}
          title={pdfTitle}
          isLoading={pdfLoading}
          error={pdfError}
          onRetry={handleRetryPdf}
          initialPage={pdfInitialPage}
        />

        {/* Instance Search Modal */}
        <InstanceSearchModal
          isOpen={instanceSearchOpen}
          onClose={() => setInstanceSearchOpen(false)}
          policyRef={instanceSearchPolicy?.policyRef || ""}
          policyTitle={instanceSearchPolicy?.policyTitle || ""}
          sourceFile={instanceSearchPolicy?.sourceFile}
          onNavigateToPage={handleNavigateToPage}
        />

        <div className="sticky bottom-0 bg-background py-6 border-t border-border">
          {/* Deep Search Mode Toggle */}
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setDeepSearchMode(!deepSearchMode)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  deepSearchMode ? "bg-rush-legacy" : "bg-gray-300"
                }`}
                role="switch"
                aria-checked={deepSearchMode}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    deepSearchMode ? "translate-x-6" : "translate-x-1"
                  }`}
                />
              </button>
              <span className="text-sm font-medium text-gray-700 flex items-center gap-1.5">
                <Search className="h-4 w-4" />
                Deep Search Mode
              </span>
              <button
                type="button"
                onClick={() => setShowDeepSearchHelp(!showDeepSearchHelp)}
                className="text-gray-400 hover:text-rush-legacy transition-colors"
                aria-label="Learn about Deep Search Mode"
              >
                <HelpCircle className="h-4 w-4" />
              </button>
            </div>

            {/* Policy Reference Input (visible when Deep Search is enabled) */}
            {deepSearchMode && (
              <div className="flex items-center gap-2">
                <label htmlFor="policy-ref" className="text-xs text-gray-600">
                  Policy Ref #:
                </label>
                <input
                  id="policy-ref"
                  type="text"
                  value={deepSearchPolicyRef}
                  onChange={(e) => setDeepSearchPolicyRef(e.target.value)}
                  placeholder="e.g., 528"
                  className="w-24 px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-rush-legacy"
                />
              </div>
            )}
          </div>

          {/* Deep Search Help Tooltip */}
          {showDeepSearchHelp && (
            <div className="mb-3 p-3 bg-rush-sage/30 border border-rush-legacy/20 rounded-lg text-sm relative">
              <button
                type="button"
                onClick={() => setShowDeepSearchHelp(false)}
                className="absolute top-2 right-2 text-gray-400 hover:text-gray-600"
                aria-label="Close help"
              >
                <X className="h-4 w-4" />
              </button>
              <p className="font-semibold text-rush-legacy mb-2">Deep Search Mode</p>
              <p className="text-gray-700 mb-2">
                Search within a <strong>specific policy</strong> to find all mentions of a term or related sections.
              </p>
              <div className="space-y-1.5 text-gray-600">
                <p><strong>How it works:</strong></p>
                <ul className="list-disc list-inside space-y-1 ml-2">
                  <li><strong>Short terms</strong> (1-2 words): Finds exact matches - e.g., "employee"</li>
                  <li><strong>Phrases/questions</strong>: Uses semantic search - e.g., "employee access to records"</li>
                </ul>
                <p className="mt-2"><strong>To use:</strong></p>
                <ol className="list-decimal list-inside space-y-1 ml-2">
                  <li>Enable the toggle above</li>
                  <li>Enter the policy reference number (e.g., 528 for HIPAA)</li>
                  <li>Type what you're looking for and press Enter</li>
                </ol>
              </div>
            </div>
          )}

          <form onSubmit={handleSubmit} className="flex gap-3">
            <Textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={deepSearchMode
                ? `Search within policy #${deepSearchPolicyRef || "___"}...`
                : "How can we help you today?"
              }
              className="resize-none min-h-[60px] focus-visible:ring-rush-legacy"
              rows={2}
              data-testid="input-message"
            />
            <Button
              type="submit"
              size="icon"
              disabled={isLoading || (deepSearchMode && !deepSearchPolicyRef.trim())}
              onClick={handleButtonClick}
              className="bg-rush-legacy hover:bg-rush-legacy h-[60px] w-[60px] flex-shrink-0"
              data-testid="button-send"
              aria-label={isLoading ? "Sending message" : "Send message"}
            >
              {isLoading ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : deepSearchMode ? (
                <Search className="h-5 w-5" />
              ) : (
                <Send className="h-5 w-5" />
              )}
            </Button>
          </form>
          <p className="text-xs text-muted-foreground mt-3 text-center">
            Press Enter to send • Shift + Enter for new line
          </p>
          <p className="text-[11px] text-muted-foreground mt-1 text-center">
            {deepSearchMode
              ? "Deep Search finds all instances of your term within a specific policy document."
              : "Every answer includes a quick summary plus the exact policy text that supports it."
            }
          </p>
        </div>
      </div>
    </section>
  );
}
