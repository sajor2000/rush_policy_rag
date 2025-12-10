"use client";

import { useState, useCallback, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Search,
  FileText,
  Loader2,
  ChevronRight,
  AlertCircle,
  Copy,
  Check,
} from "lucide-react";
import {
  searchInstances,
  type TermInstance,
  type InstanceSearchResponse,
} from "@/lib/api";

interface InstanceSearchModalProps {
  isOpen: boolean;
  onClose: () => void;
  policyRef: string;
  policyTitle: string;
  sourceFile?: string;
  onNavigateToPage?: (page: number, sourceFile: string) => void;
  initialSearchTerm?: string;
}

export default function InstanceSearchModal({
  isOpen,
  onClose,
  policyRef,
  policyTitle,
  sourceFile,
  onNavigateToPage,
  initialSearchTerm = "",
}: InstanceSearchModalProps) {
  const [searchTerm, setSearchTerm] = useState(initialSearchTerm);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<InstanceSearchResponse | null>(null);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  // Helper to copy text to clipboard
  const handleCopyText = useCallback(async (text: string, index: number) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedIndex(index);
      setTimeout(() => setCopiedIndex(null), 2000);
    } catch (err) {
      console.error("Failed to copy:", err);
    }
  }, []);

  // Helper to format page display - show estimated range since exact page may not be available
  const formatPageDisplay = (pageNumber: number | null): string => {
    if (!pageNumber) return "N/A";
    // Show as estimated range (±1 page) since page numbers may be approximate
    const minPage = Math.max(1, pageNumber - 1);
    const maxPage = pageNumber + 1;
    return `~${minPage}-${maxPage}`;
  };

  // Reset state when modal opens
  useEffect(() => {
    if (isOpen) {
      setSearchTerm(initialSearchTerm);
      setError(null);
      setResults(null);
    }
  }, [isOpen, initialSearchTerm]);

  const handleSearch = useCallback(async () => {
    if (!searchTerm.trim()) {
      setError("Please enter a search term");
      return;
    }

    setIsSearching(true);
    setError(null);
    setResults(null);

    try {
      const response = await searchInstances(
        policyRef,
        searchTerm.trim(),
        caseSensitive
      );
      setResults(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setIsSearching(false);
    }
  }, [policyRef, searchTerm, caseSensitive]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !isSearching) {
        handleSearch();
      }
    },
    [handleSearch, isSearching]
  );

  const handleInstanceClick = useCallback(
    (instance: TermInstance) => {
      if (instance.page_number && sourceFile && onNavigateToPage) {
        onNavigateToPage(instance.page_number, sourceFile);
        onClose();
      }
    },
    [sourceFile, onNavigateToPage, onClose]
  );

  const renderHighlightedContext = (instance: TermInstance) => {
    const { context, highlight_start, highlight_end } = instance;

    // If no highlight positions, just return the context
    if (highlight_start === 0 && highlight_end === 0) {
      return <span className="text-sm text-gray-700">{context}</span>;
    }

    const before = context.slice(0, highlight_start);
    const highlighted = context.slice(highlight_start, highlight_end);
    const after = context.slice(highlight_end);

    return (
      <span className="text-sm text-gray-700">
        {before}
        <mark className="bg-yellow-200 px-0.5 rounded font-medium">
          {highlighted}
        </mark>
        {after}
      </span>
    );
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-rush-legacy">
            <Search className="h-5 w-5" />
            Search within Policy
          </DialogTitle>
          <DialogDescription>
            Find text or sections in{" "}
            <span className="font-medium text-foreground">{policyTitle}</span>{" "}
            (Ref #{policyRef})
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-4">
          <div className="flex gap-2">
            <div className="flex-1">
              <Label htmlFor="search-term" className="sr-only">
                Search term
              </Label>
              <Input
                id="search-term"
                placeholder="Enter term or phrase to search for..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isSearching}
                autoFocus
                className="focus-visible:ring-rush-legacy"
              />
            </div>
            <Button
              onClick={handleSearch}
              disabled={isSearching || !searchTerm.trim()}
              className="bg-rush-legacy hover:bg-rush-legacy/90"
            >
              {isSearching ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Search className="h-4 w-4" />
              )}
            </Button>
          </div>

          <div className="flex items-center gap-2">
            <Checkbox
              id="case-sensitive"
              checked={caseSensitive}
              onCheckedChange={(checked) => setCaseSensitive(checked === true)}
            />
            <Label
              htmlFor="case-sensitive"
              className="text-sm text-muted-foreground cursor-pointer"
            >
              Case sensitive
            </Label>
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
            <AlertCircle className="h-4 w-4 flex-shrink-0" />
            {error}
          </div>
        )}

        {results && (
          <div className="flex-1 overflow-y-auto space-y-2 pr-1">
            <div className="text-sm text-muted-foreground mb-3">
              Found{" "}
              <span className="font-semibold text-foreground">
                {results.total_instances}
              </span>{" "}
              result{results.total_instances !== 1 ? "s" : ""} for &quot;
              {results.search_term}&quot;
            </div>

            {results.total_instances === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <FileText className="h-12 w-12 mx-auto mb-3 opacity-50" />
                <p>No results found in this policy.</p>
                <p className="text-sm mt-1">Try a different search term or phrase.</p>
              </div>
            ) : (
              <div className="space-y-3">
                {results.instances.map((instance, idx) => (
                  <div
                    key={`${instance.chunk_id}-${instance.position}-${idx}`}
                    className="p-3 rounded-lg border bg-white border-gray-200"
                  >
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="inline-flex items-center px-2 py-0.5 text-xs font-medium bg-rush-sage/40 text-rush-legacy rounded" title="Page numbers are estimated from chunk position">
                          Pages {formatPageDisplay(instance.page_number)}
                        </span>
                        {instance.section && (
                          <span className="text-xs text-muted-foreground">
                            Section {instance.section}
                            {instance.section_title &&
                              `: ${instance.section_title}`}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1 flex-shrink-0">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleCopyText(instance.context, idx);
                          }}
                          className="p-1.5 text-gray-400 hover:text-rush-legacy hover:bg-gray-100 rounded transition-colors"
                          title="Copy text to search in PDF (Ctrl+F)"
                        >
                          {copiedIndex === idx ? (
                            <Check className="h-3.5 w-3.5 text-green-600" />
                          ) : (
                            <Copy className="h-3.5 w-3.5" />
                          )}
                        </button>
                        {instance.page_number && sourceFile && (
                          <button
                            onClick={() => handleInstanceClick(instance)}
                            className="p-1.5 text-rush-legacy hover:bg-rush-sage/30 rounded transition-colors"
                            title="Jump to this section in PDF"
                          >
                            <ChevronRight className="h-4 w-4" />
                          </button>
                        )}
                      </div>
                    </div>
                    {/* Show FULL chunk content - no truncation */}
                    <div className="leading-relaxed break-words text-sm text-gray-700 bg-gray-50 p-2 rounded border border-gray-100">
                      {renderHighlightedContext(instance)}
                    </div>
                  </div>
                ))}
                {/* Disclaimer about page estimates */}
                <p className="text-xs text-gray-400 text-center mt-3 italic">
                  💡 Page numbers are estimated. Use "Copy" button to search exact text in PDF (Ctrl+F).
                </p>
              </div>
            )}
          </div>
        )}

        <div className="flex justify-end pt-4 border-t">
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
