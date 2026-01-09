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
import {
  sendMessage,
  searchInstances,
  type Source,
  type Evidence,
} from "@/lib/api";

interface Message {
  role: "user" | "assistant";
  content: string;
  summary?: string;
  evidence?: Evidence[];
  sources?: Source[];
  rawResponse?: string;
  found?: boolean;
  // For Deep Search results - enables "View PDF" button
  deepSearchPolicy?: {
    policyRef: string;
    policyTitle: string;
    sourceFile?: string;
  };
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

  // Device ambiguity clarification state
  const [showClarification, setShowClarification] = useState<{
    ambiguous_term: string;
    message: string;
    options: Array<{
      label: string;
      expansion: string;
      type: string;
    }>;
    originalQuery: string;
  } | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Shared helper function to fetch and display PDF
  const fetchAndDisplayPdf = async (
    sourceFile: string,
    title: string,
    initialPage: number = 1
  ) => {
    setLastPdfSourceFile(sourceFile);
    setPdfUrl(null);
    setPdfError(null);
    setPdfLoading(true);
    setPdfTitle(title);
    setPdfViewerOpen(true);
    setPdfInitialPage(initialPage);

    try {
      const response = await fetch(`/api/pdf/${encodeURIComponent(sourceFile)}`);

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
      setPdfError(err instanceof Error ? err.message : "Failed to load PDF");
      setPdfUrl(null);
    } finally {
      setPdfLoading(false);
    }
  };

  const handleViewPdf = (sourceFile: string, title: string, pageNumber?: number) => {
    fetchAndDisplayPdf(sourceFile, title, pageNumber || 1);
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
    if (isLoading) return; // Prevent duplicate requests
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
          // Show estimated page range (±1 page) since exact page numbers may not be available
          let pageInfo = "N/A";
          if (instance.page_number) {
            const minPage = Math.max(1, instance.page_number - 1);
            const maxPage = instance.page_number + 1;
            pageInfo = `Pages ~${minPage}-${maxPage}`;
          }
          const sectionInfo = instance.section ? `, Section ${instance.section}` : "";
          responseContent += `**${idx + 1}. ${pageInfo}${sectionInfo}**\n`;
          // Show FULL chunk content - no truncation to help users find exact text
          responseContent += `> "${instance.context.trim()}"\n\n`;
        });

        if (result.total_instances > 10) {
          responseContent += `_Showing first 10 of ${result.total_instances} results._\n\n`;
        }

        // Add helpful tip (View PDF button is rendered by ChatMessage component)
        responseContent += `---\n_Page numbers are estimated. Use Ctrl+F in PDF to find exact text._`;
      }

      // Build policy info for View PDF button
      const policyInfo = result.source_file ? {
        policyRef: result.policy_ref,
        policyTitle: result.policy_title,
        sourceFile: result.source_file
      } : undefined;

      setMessages((prev) => [...prev, {
        role: "assistant",
        content: responseContent,
        found: result.total_instances > 0,
        deepSearchPolicy: policyInfo
      }]);

      // Also store for potential modal usage
      if (policyInfo) {
        setInstanceSearchPolicy(policyInfo);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Deep search failed");
    } finally {
      setIsLoading(false);
    }
  };

  // Handler for navigating to a specific page in PDF from instance search results
  const handleNavigateToPage = (pageNumber: number, sourceFile?: string) => {
    setInstanceSearchOpen(false);

    const fileToOpen = sourceFile || instanceSearchPolicy?.sourceFile || lastPdfSourceFile;
    const titleToUse = instanceSearchPolicy?.policyTitle || pdfTitle;

    if (!fileToOpen) {
      setError("Unable to open PDF: source file not available");
      return;
    }

    fetchAndDisplayPdf(fileToOpen, titleToUse, pageNumber);
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

    // Normal Q&A mode (non-streaming)
    setMessages((prev) => [...prev, { role: "user", content: userMessage }]);
    setIsLoading(true);

    try {
      const result = await sendMessage(userMessage);

      // Check if clarification is needed (e.g., ambiguous device terms like "IV")
      if (result.confidence === "clarification_needed" && result.clarification) {
        setShowClarification({
          ...result.clarification,
          originalQuery: userMessage,
        });
        setIsLoading(false);
        return;
      }

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.summary || result.response,
          summary: result.summary || result.response,
          evidence: result.evidence || [],
          sources: result.sources || [],
          rawResponse: result.raw_response,
          found: result.found !== undefined ? result.found : (result.evidence?.length ?? 0) > 0,
        },
      ]);
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

  const handleClarificationChoice = async (option: { label: string; expansion: string; type: string }) => {
    if (!showClarification) return;

    // Create refined query with the selected expansion
    const refinedQuery = `${showClarification.originalQuery} ${option.expansion}`;

    // Clear clarification prompt
    setShowClarification(null);
    setIsLoading(true);

    try {
      const result = await sendMessage(refinedQuery);

      // Should not need clarification after refinement, but check anyway
      if (result.confidence === "clarification_needed" && result.clarification) {
        setShowClarification({
          ...result.clarification,
          originalQuery: refinedQuery
        });
        setIsLoading(false);
        return;
      }

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

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
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
                role={message.role}
                content={message.content}
                summary={message.summary}
                evidence={message.evidence}
                sources={message.sources}
                found={message.found}
                deepSearchPolicy={message.deepSearchPolicy}
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
            {/* Device Ambiguity Clarification UI */}
            {showClarification && (
              <div className="p-4 bg-amber-50 border-l-4 border-amber-400 rounded-lg mb-4 shadow-md">
                <p className="text-sm font-medium text-gray-800 mb-3">
                  {showClarification.message}
                </p>
                <div className="flex flex-col gap-2">
                  {showClarification.options.map((opt, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleClarificationChoice(opt)}
                      className="text-left px-4 py-2 bg-white border border-rush-legacy/30 rounded-md hover:bg-rush-legacy/5 transition-colors"
                    >
                      <span className="font-medium text-rush-legacy">{opt.label}</span>
                    </button>
                  ))}
                </div>
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
          {/* Mode Toggle: Classic Q&A vs Search Within Policy */}
          <div className="mb-3">
            {/* Mode Indicator */}
            <div className="flex items-center justify-center gap-3 mb-2">
              <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                !deepSearchMode
                  ? "bg-rush-legacy text-white"
                  : "bg-gray-100 text-gray-500"
              }`}>
                Classic Q&A {!deepSearchMode && <span className="text-[10px] opacity-80">(Default)</span>}
              </span>
              <button
                type="button"
                onClick={() => {
                  setDeepSearchMode(!deepSearchMode);
                  setError(null); // Clear error when switching modes
                }}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  deepSearchMode ? "bg-rush-growth" : "bg-gray-300"
                }`}
                role="switch"
                aria-checked={deepSearchMode}
                aria-label="Toggle between Classic Q&A and Search Within Policy modes"
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    deepSearchMode ? "translate-x-6" : "translate-x-1"
                  }`}
                />
              </button>
              <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                deepSearchMode
                  ? "bg-rush-growth text-white"
                  : "bg-gray-100 text-gray-500"
              }`}>
                Search Within Policy
              </span>
              <button
                type="button"
                onClick={() => setShowDeepSearchHelp(!showDeepSearchHelp)}
                className="text-gray-400 hover:text-rush-legacy transition-colors"
                aria-label={showDeepSearchHelp ? "Close help" : "Learn about search modes"}
                aria-expanded={showDeepSearchHelp}
              >
                <HelpCircle className="h-4 w-4" />
              </button>
            </div>

            {/* Policy Reference Input (visible when Search Within Policy is enabled) */}
            {deepSearchMode && (
              <div className="flex flex-col items-center gap-1">
                <div className="flex items-center gap-2">
                  <label htmlFor="policy-ref" className="text-xs text-gray-600 flex items-center gap-1">
                    <Search className="h-3.5 w-3.5" />
                    Policy Ref #:
                  </label>
                  <input
                    id="policy-ref"
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    value={deepSearchPolicyRef}
                    onChange={(e) => {
                      // Only allow numeric input
                      const value = e.target.value.replace(/[^\d]/g, '');
                      setDeepSearchPolicyRef(value);
                    }}
                    placeholder="e.g., 528"
                    className="w-24 px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-rush-growth"
                  />
                </div>
                {!deepSearchPolicyRef && (
                  <p className="text-[10px] text-gray-500">
                    Don&apos;t know the policy number? Switch to Classic Q&A to find it first.
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Search Modes Help Tooltip */}
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
              <p className="font-semibold text-rush-legacy mb-3">Two Search Modes</p>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Classic Q&A Mode */}
                <div className="bg-white/50 p-2.5 rounded border border-rush-legacy/10">
                  <p className="font-semibold text-rush-legacy flex items-center gap-1.5 mb-1">
                    <Send className="h-3.5 w-3.5" />
                    Classic Q&A
                    <span className="text-[9px] bg-rush-legacy/20 text-rush-legacy px-1.5 py-0.5 rounded font-normal">Recommended</span>
                  </p>
                  <p className="text-gray-600 text-xs mb-1.5">
                    Ask any policy question. Get a summarized answer + evidence from matching policies.
                  </p>
                  <p className="text-gray-700 text-[10px] font-medium mb-1">Use when:</p>
                  <ul className="text-gray-600 text-[10px] mb-1.5 space-y-0.5 ml-2">
                    <li>• You have a question and don&apos;t know which policy covers it</li>
                    <li>• You want a summarized answer with citations</li>
                  </ul>
                  <p className="text-gray-500 text-[10px] italic">
                    Examples: "What is the visitor policy?" • "When can employees access their records?"
                  </p>
                </div>

                {/* Search Within Policy Mode */}
                <div className="bg-white/50 p-2.5 rounded border border-rush-growth/20">
                  <p className="font-semibold text-rush-growth flex items-center gap-1.5 mb-1">
                    <Search className="h-3.5 w-3.5" />
                    Search Within Policy
                    <span className="text-[9px] bg-rush-growth/20 text-rush-growth px-1.5 py-0.5 rounded font-normal">Advanced</span>
                  </p>
                  <p className="text-gray-600 text-xs mb-1.5">
                    Find ALL instances of a word or phrase within a specific policy you already know.
                  </p>
                  <p className="text-gray-700 text-[10px] font-medium mb-1">Use when:</p>
                  <ul className="text-gray-600 text-[10px] mb-1.5 space-y-0.5 ml-2">
                    <li>• You know the policy number but need to find specific text</li>
                    <li>• You want to see every mention of a term (e.g., "employee")</li>
                    <li>• You need page/section numbers for a long policy</li>
                  </ul>
                  <p className="text-gray-500 text-[10px] italic">
                    Examples: Find "employee" in #528 • Find "prohibited" in #1515
                  </p>
                </div>
              </div>

              <p className="text-gray-600 text-[10px] mt-3 text-center bg-white/50 py-1.5 px-2 rounded">
                Don&apos;t know the policy number? Use Classic Q&A first to find it, then switch to Search Within Policy.
              </p>
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
              ? "Search Within Policy: Find ALL mentions of a word or phrase in a specific policy. Shows page numbers."
              : "Classic Q&A (Recommended): Ask any question, get a summarized answer with evidence from matching policies."
            }
          </p>
        </div>
      </div>
    </section>
  );
}
