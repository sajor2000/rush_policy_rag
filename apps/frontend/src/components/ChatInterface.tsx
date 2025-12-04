"use client";

import { useState, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Send, Sparkles, Loader2 } from "lucide-react";
import ChatMessage from "./ChatMessage";
import LoadingState from "./LoadingState";
import ErrorMessage from "./ErrorMessage";
import PDFViewer from "./PDFViewer";
import { sendMessage, type Source, type Evidence } from "@/lib/api";

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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!input.trim() || isLoading) return;

    const userMessage = input.trim();
    setInput("");
    setError(null);

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
      const syntheticEvent = { preventDefault: () => {} } as React.FormEvent;
      // Use the textarea value directly for submission
      const userMessage = textareaValue.trim();
      setInput("");
      setError(null);
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
        />

        <div className="sticky bottom-0 bg-background py-6 border-t border-border">
          <form onSubmit={handleSubmit} className="flex gap-3">
            <Textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="How can we help you today?"
              className="resize-none min-h-[60px] focus-visible:ring-rush-legacy"
              rows={2}
              data-testid="input-message"
            />
            <Button
              type="submit"
              size="icon"
              disabled={isLoading}
              onClick={handleButtonClick}
              className="bg-rush-legacy hover:bg-rush-legacy h-[60px] w-[60px] flex-shrink-0"
              data-testid="button-send"
              aria-label={isLoading ? "Sending message" : "Send message"}
            >
              {isLoading ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : (
                <Send className="h-5 w-5" />
              )}
            </Button>
          </form>
          <p className="text-xs text-muted-foreground mt-3 text-center">
            Press Enter to send • Shift + Enter for new line
          </p>
          <p className="text-[11px] text-muted-foreground mt-1 text-center">
            Every answer includes a quick summary plus the exact policy text that supports it.
          </p>
        </div>
      </div>
    </section>
  );
}
