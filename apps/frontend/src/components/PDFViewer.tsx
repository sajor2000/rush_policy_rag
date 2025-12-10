"use client";

import { useState, useCallback, useEffect } from "react";
import dynamic from "next/dynamic";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  Download,
  X,
  Loader2,
  RefreshCw,
} from "lucide-react";

import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// Dynamically import react-pdf to avoid SSR issues with DOMMatrix
const Document = dynamic(
  () => import("react-pdf").then((mod) => mod.Document),
  { ssr: false }
);
const Page = dynamic(
  () => import("react-pdf").then((mod) => mod.Page),
  { ssr: false }
);

// Configure PDF.js worker on client side only
if (typeof window !== "undefined") {
  import("react-pdf").then((pdfjs) => {
    pdfjs.pdfjs.GlobalWorkerOptions.workerSrc = `/pdf.worker.min.mjs`;
  });
}

interface PDFViewerProps {
  isOpen: boolean;
  onClose: () => void;
  pdfUrl: string | null;
  title?: string;
  isLoading?: boolean;  // Loading state from parent (fetching URL)
  error?: string | null; // Error from parent (URL fetch failed)
  onRetry?: () => void;  // Callback to retry loading PDF
  initialPage?: number;  // Jump to this page when opening
}

export default function PDFViewer({
  isOpen,
  onClose,
  pdfUrl,
  title,
  isLoading: parentLoading = false,
  error: parentError = null,
  onRetry,
  initialPage = 1,
}: PDFViewerProps) {
  const [numPages, setNumPages] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [scale, setScale] = useState(1.0);
  const [pdfLoading, setPdfLoading] = useState(false); // PDF document loading
  const [pdfError, setPdfError] = useState<string | null>(null); // PDF parse error
  const handleAnnotationClick = useCallback(
    ({ pageNumber: targetPageNumber }: { pageNumber: number }) => {
      // PDF internal links (table of contents, related links) should navigate within the viewer
      if (targetPageNumber && targetPageNumber !== pageNumber) {
        setPageNumber(targetPageNumber);
      }
    },
    [pageNumber]
  );
  
  // Combined loading and error states
  const loading = parentLoading || pdfLoading;
  const error = parentError || pdfError;

  // When we receive a new URL, mark PDF as loading
  useEffect(() => {
    if (pdfUrl) {
      setPdfLoading(true);
      setPdfError(null);
    }
  }, [pdfUrl]);

  const onDocumentLoadSuccess = useCallback(
    ({ numPages }: { numPages: number }) => {
      setNumPages(numPages);
      // Jump to initialPage if valid, otherwise page 1
      const targetPage = Math.max(1, Math.min(initialPage, numPages));
      setPageNumber(targetPage);
      setPdfLoading(false);
      setPdfError(null);
    },
    [initialPage]
  );

  const onDocumentLoadError = useCallback((err: Error) => {
    console.error("PDF load error:", err);
    setPdfError("Failed to load PDF document. Please try again.");
    setPdfLoading(false);
  }, []);

  const goToPrevPage = useCallback(
    () => setPageNumber((prev) => Math.max(prev - 1, 1)),
    []
  );
  const goToNextPage = useCallback(
    () => setPageNumber((prev) => Math.min(prev + 1, numPages || prev)),
    [numPages]
  );
  const zoomIn = useCallback(
    () => setScale((prev) => Math.min(prev + 0.25, 2.5)),
    []
  );
  const zoomOut = useCallback(
    () => setScale((prev) => Math.max(prev - 0.25, 0.5)),
    []
  );

  // Keyboard navigation
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      // Only handle if no input element is focused
      if (
        document.activeElement?.tagName === "INPUT" ||
        document.activeElement?.tagName === "TEXTAREA"
      ) {
        return;
      }

      switch (e.key) {
        case "ArrowLeft":
        case "PageUp":
          e.preventDefault();
          goToPrevPage();
          break;
        case "ArrowRight":
        case "PageDown":
          e.preventDefault();
          goToNextPage();
          break;
        case "+":
        case "=":
          e.preventDefault();
          zoomIn();
          break;
        case "-":
        case "_":
          e.preventDefault();
          zoomOut();
          break;
        case "Escape":
          e.preventDefault();
          onClose();
          break;
        case "Home":
          e.preventDefault();
          setPageNumber(1);
          break;
        case "End":
          if (numPages) {
            e.preventDefault();
            setPageNumber(numPages);
          }
          break;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, goToPrevPage, goToNextPage, zoomIn, zoomOut, onClose, numPages]);

  const handleOpenChange = (open: boolean) => {
    if (!open) {
      onClose();
      setPageNumber(1);
      setScale(1.0);
      setPdfLoading(false);
      setPdfError(null);
      setNumPages(null);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-4xl h-[90vh] flex flex-col p-0">
        <DialogHeader className="px-4 py-3 border-b flex-shrink-0">
          <div className="flex items-center justify-between">
            <DialogTitle className="text-lg font-semibold truncate pr-4">
              {title || "Policy Document"}
            </DialogTitle>
            <Button
              variant="ghost"
              size="icon"
              onClick={onClose}
              className="h-8 w-8"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
          <DialogDescription className="sr-only">
            View and navigate the policy document PDF
          </DialogDescription>
        </DialogHeader>

        {/* Controls */}
        <div className="flex items-center justify-between px-4 py-2 border-b bg-muted/50 flex-shrink-0">
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="icon"
              onClick={goToPrevPage}
              disabled={pageNumber <= 1}
              className="h-8 w-8"
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span className="text-sm min-w-[100px] text-center">
              Page {pageNumber} of {numPages || "..."}
            </span>
            <Button
              variant="outline"
              size="icon"
              onClick={goToNextPage}
              disabled={pageNumber >= (numPages || 1)}
              className="h-8 w-8"
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="icon"
              onClick={zoomOut}
              disabled={scale <= 0.5}
              className="h-8 w-8"
            >
              <ZoomOut className="h-4 w-4" />
            </Button>
            <span className="text-sm min-w-[60px] text-center">
              {Math.round(scale * 100)}%
            </span>
            <Button
              variant="outline"
              size="icon"
              onClick={zoomIn}
              disabled={scale >= 2.5}
              className="h-8 w-8"
            >
              <ZoomIn className="h-4 w-4" />
            </Button>

            {pdfUrl && (
              <Button
                variant="outline"
                size="icon"
                asChild
                className="h-8 w-8 ml-2"
              >
                <a href={pdfUrl} target="_blank" rel="noopener noreferrer">
                  <Download className="h-4 w-4" />
                </a>
              </Button>
            )}
          </div>
        </div>

        {/* PDF Content */}
        <div className="flex-1 overflow-auto bg-gray-100 flex justify-center">
          {loading && (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="h-8 w-8 animate-spin text-rush-legacy" />
            </div>
          )}

          {error && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center text-red-600 p-4">
                <p className="mb-2 font-medium">Unable to load document</p>
                <p className="text-sm text-gray-600 mb-4">{error}</p>
                <div className="flex gap-2 justify-center mt-4">
                  {onRetry && (
                    <Button
                      variant="default"
                      className="bg-rush-legacy hover:bg-rush-legacy/90"
                      onClick={onRetry}
                    >
                      <RefreshCw className="h-4 w-4 mr-2" />
                      Try Again
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    onClick={onClose}
                  >
                    Close
                  </Button>
                </div>
              </div>
            </div>
          )}

          {pdfUrl && !error && (
            <Document
              file={pdfUrl}
              onLoadSuccess={onDocumentLoadSuccess}
              onLoadError={onDocumentLoadError}
              loading={null}
              onItemClick={handleAnnotationClick}
              externalLinkTarget="_blank"
              className="py-4"
            >
              <Page
                pageNumber={pageNumber}
                scale={scale}
                className="shadow-lg"
                renderTextLayer={true}
                renderAnnotationLayer={true}
              />
            </Document>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
