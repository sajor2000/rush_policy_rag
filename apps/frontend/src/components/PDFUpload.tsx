"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Upload,
  FileText,
  CheckCircle2,
  XCircle,
  Loader2,
  ChevronDown,
  ChevronUp
} from "lucide-react";
import { uploadPDF, getUploadStatus, type UploadResponse, type UploadStatus } from "@/lib/api";

interface UploadState {
  status: "idle" | "uploading" | "processing" | "completed" | "failed";
  progress: number;
  jobId?: string;
  filename?: string;
  chunksCreated?: number;
  error?: string;
}

export default function PDFUpload() {
  const [uploadState, setUploadState] = useState<UploadState>({
    status: "idle",
    progress: 0,
  });
  const [isDragOver, setIsDragOver] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Clean up polling on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, []);

  const startPolling = useCallback((jobId: string) => {
    // Clear any existing interval
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
    }

    const poll = async () => {
      try {
        const status = await getUploadStatus(jobId);

        setUploadState(prev => ({
          ...prev,
          progress: status.progress,
          chunksCreated: status.chunks_created,
          status: mapBackendStatus(status.status),
          error: status.error,
        }));

        // Stop polling if completed or failed
        if (status.status === "completed" || status.status === "failed") {
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }
        }
      } catch (error) {
        console.error("Error polling status:", error);
      }
    };

    // Poll immediately, then every 2 seconds
    poll();
    pollIntervalRef.current = setInterval(poll, 2000);
  }, []);

  const mapBackendStatus = (status: string): UploadState["status"] => {
    switch (status) {
      case "queued":
      case "uploading":
        return "uploading";
      case "processing":
      case "indexing":
        return "processing";
      case "completed":
        return "completed";
      case "failed":
        return "failed";
      default:
        return "processing";
    }
  };

  const handleFile = async (file: File) => {
    // Validate file type
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setUploadState({
        status: "failed",
        progress: 0,
        error: "Only PDF files are supported",
      });
      return;
    }

    // Validate file size (50MB)
    const maxSize = 50 * 1024 * 1024;
    if (file.size > maxSize) {
      setUploadState({
        status: "failed",
        progress: 0,
        error: "File exceeds 50MB limit",
      });
      return;
    }

    setUploadState({
      status: "uploading",
      progress: 5,
      filename: file.name,
    });

    try {
      const response = await uploadPDF(file);

      setUploadState(prev => ({
        ...prev,
        jobId: response.job_id,
        progress: 10,
      }));

      // Start polling for status
      startPolling(response.job_id);
    } catch (error) {
      setUploadState({
        status: "failed",
        progress: 0,
        filename: file.name,
        error: error instanceof Error ? error.message : "Upload failed",
      });
    }
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);

    const file = e.dataTransfer.files[0];
    if (file) {
      handleFile(file);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      handleFile(file);
    }
  };

  const handleReset = () => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    setUploadState({
      status: "idle",
      progress: 0,
    });
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const renderStatus = () => {
    switch (uploadState.status) {
      case "uploading":
        return (
          <div className="flex items-center gap-2 text-blue-600">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>Uploading {uploadState.filename}...</span>
          </div>
        );
      case "processing":
        return (
          <div className="flex items-center gap-2 text-amber-600">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>Processing... {uploadState.chunksCreated ? `${uploadState.chunksCreated} chunks created` : ""}</span>
          </div>
        );
      case "completed":
        return (
          <div className="flex items-center gap-2 text-green-600">
            <CheckCircle2 className="h-4 w-4" />
            <span>Completed! {uploadState.chunksCreated} chunks indexed and ready for search.</span>
          </div>
        );
      case "failed":
        return (
          <div className="flex items-center gap-2 text-red-600">
            <XCircle className="h-4 w-4" />
            <span>{uploadState.error || "Upload failed"}</span>
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <Card className="w-full border-border">
      <CardHeader
        className="cursor-pointer hover:bg-muted/50 transition-colors"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <Upload className="h-5 w-5 text-rush-legacy" />
            Upload Policy Document
          </CardTitle>
          {isExpanded ? (
            <ChevronUp className="h-5 w-5 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-5 w-5 text-muted-foreground" />
          )}
        </div>
      </CardHeader>

      {isExpanded && (
        <CardContent className="space-y-4">
          {uploadState.status === "idle" ? (
            <div
              className={`
                border-2 border-dashed rounded-lg p-8 text-center transition-colors
                ${isDragOver
                  ? "border-rush-legacy bg-rush-sage/30"
                  : "border-border hover:border-rush-legacy/50"
                }
              `}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf"
                onChange={handleFileSelect}
                className="hidden"
                aria-label="Upload PDF file"
              />

              <FileText className="h-12 w-12 mx-auto text-muted-foreground mb-4" />

              <p className="text-muted-foreground mb-4">
                Drag and drop a PDF file here, or
              </p>

              <Button
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
                className="border-rush-legacy text-rush-legacy hover:bg-rush-sage"
              >
                Browse Files
              </Button>

              <p className="text-xs text-muted-foreground mt-4">
                Maximum file size: 50MB
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {uploadState.filename && (
                <div className="flex items-center gap-2 text-sm">
                  <FileText className="h-4 w-4" />
                  <span className="font-medium">{uploadState.filename}</span>
                </div>
              )}

              <Progress value={uploadState.progress} className="h-2" />

              <div className="flex items-center justify-between">
                {renderStatus()}
                <span className="text-sm text-muted-foreground">
                  {uploadState.progress}%
                </span>
              </div>

              {(uploadState.status === "completed" || uploadState.status === "failed") && (
                <Button
                  variant="outline"
                  onClick={handleReset}
                  className="w-full"
                >
                  Upload Another File
                </Button>
              )}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}
