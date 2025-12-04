export interface Source {
  citation: string;
  source_file: string;
  title: string;
  reference_number?: string;
  section?: string;
  applies_to?: string;
  date_updated?: string;
  date_approved?: string;
  document_owner?: string;
  match_type?: "verified" | "related";  // verified = exact match, related = fallback
}

export interface Evidence {
  snippet: string;
  citation: string;
  title: string;
  reference_number?: string;
  section?: string;
  applies_to?: string;
  document_owner?: string;
  date_updated?: string;
  date_approved?: string;
  source_file?: string;
  page_number?: number;  // 1-indexed page number for PDF navigation
  match_type?: "verified" | "related";  // verified = exact match, related = fallback
}

export interface ChatApiResponse {
  response: string;
  summary?: string;
  evidence?: Evidence[];
  sources?: Source[];
  raw_response?: string;
  found?: boolean;
  chunks_used?: number;
  error?: string;
}

// Constants
const MAX_MESSAGE_LENGTH = 2000;
const REQUEST_TIMEOUT_MS = 60000; // Increased from 30s for RAG operations
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1000;

// Rate limit tracking
let rateLimitResetTime: number | null = null;

/**
 * Check if we're currently rate limited.
 */
export function isRateLimited(): boolean {
  return rateLimitResetTime !== null && Date.now() < rateLimitResetTime;
}

/**
 * Get time until rate limit resets in seconds.
 */
export function getRateLimitResetSeconds(): number {
  if (!rateLimitResetTime) return 0;
  const remaining = Math.ceil((rateLimitResetTime - Date.now()) / 1000);
  return Math.max(0, remaining);
}

/**
 * Send a chat message with timeout, retry, and validation.
 */
export async function sendMessage(message: string): Promise<ChatApiResponse> {
  // Check if rate limited before sending
  if (isRateLimited()) {
    const waitTime = getRateLimitResetSeconds();
    throw new Error(`Rate limited. Please wait ${waitTime} seconds before trying again.`);
  }

  // Client-side validation
  if (!message || !message.trim()) {
    throw new Error("Message cannot be empty");
  }

  const normalizedMessage = message.trim();
  if (normalizedMessage.length > MAX_MESSAGE_LENGTH) {
    throw new Error(`Message exceeds maximum length of ${MAX_MESSAGE_LENGTH} characters`);
  }

  let lastError: Error | null = null;

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message: normalizedMessage }),
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      // Try to parse JSON response
      let data: Record<string, unknown>;
      try {
        data = await response.json();
      } catch {
        if (!response.ok) {
          throw new Error(`Server error (${response.status}): Unable to process request`);
        }
        throw new Error("Invalid response from server");
      }

      // Handle error responses
      if (!response.ok) {
        const errorMessage =
          (typeof data.detail === 'string' ? data.detail : null) ||
          (typeof data.error === 'string' ? data.error : null) ||
          (typeof data.message === 'string' ? data.message : null) ||
          `Request failed (${response.status})`;

        // Handle 429 Rate Limit with Retry-After header
        if (response.status === 429) {
          const retryAfter = parseInt(response.headers.get('Retry-After') || '60', 10);
          rateLimitResetTime = Date.now() + (retryAfter * 1000);

          if (attempt < MAX_RETRIES) {
            lastError = new Error(`Rate limited. Waiting ${retryAfter}s before retry...`);
            await sleep(retryAfter * 1000);
            continue;
          }
          throw new Error(`Rate limited. Please wait ${retryAfter} seconds before trying again.`);
        }

        // Handle 503 Service Unavailable (circuit breaker open)
        if (response.status === 503) {
          const retryAfter = parseInt(response.headers.get('Retry-After') || '10', 10);
          if (attempt < MAX_RETRIES) {
            lastError = new Error(errorMessage);
            await sleep(retryAfter * 1000);
            continue;
          }
          throw new Error("Service temporarily unavailable. Please try again in a few moments.");
        }

        // Handle 504 Gateway Timeout
        if (response.status === 504) {
          if (attempt < MAX_RETRIES) {
            lastError = new Error("Request timed out. Retrying...");
            await sleep(RETRY_DELAY_MS * attempt);
            continue;
          }
          throw new Error("Request timed out. Please try again.");
        }

        // Retry on other 5xx errors
        if (response.status >= 500 && attempt < MAX_RETRIES) {
          lastError = new Error(errorMessage);
          await sleep(RETRY_DELAY_MS * attempt);
          continue;
        }

        throw new Error(errorMessage);
      }

      // Validate required fields
      if (typeof data.response !== 'string') {
        throw new Error("Invalid response format from server");
      }

      return {
        response: data.response as string,
        summary: (data.summary as string) ?? data.response as string,
        evidence: Array.isArray(data.evidence) ? data.evidence as Evidence[] : [],
        sources: Array.isArray(data.sources) ? data.sources as Source[] : [],
        raw_response: data.raw_response as string | undefined,
        found: typeof data.found === 'boolean' ? data.found : undefined,
        chunks_used: typeof data.chunks_used === 'number' ? data.chunks_used : undefined,
        error: typeof data.error === 'string' ? data.error : undefined,
      };

    } catch (err) {
      clearTimeout(timeoutId);

      if (err instanceof Error) {
        if (err.name === 'AbortError') {
          throw new Error("Request timed out. Please try again.");
        }
        lastError = err;
      } else {
        lastError = new Error("An unexpected error occurred");
      }

      // Don't retry on client errors
      if (lastError.message.includes("Message") || lastError.message.includes("empty")) {
        throw lastError;
      }

      // Retry on network errors
      if (attempt < MAX_RETRIES) {
        await sleep(RETRY_DELAY_MS * attempt);
        continue;
      }
    }
  }

  throw lastError || new Error("Failed to send message after multiple attempts");
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ============================================================================
// PDF Upload API
// ============================================================================

export interface UploadResponse {
  job_id: string;
  filename: string;
  status: string;
  message: string;
}

export interface UploadStatus {
  job_id: string;
  filename: string;
  status: "queued" | "uploading" | "processing" | "indexing" | "completed" | "failed";
  progress: number;
  chunks_created: number;
  error?: string;
  created_at: string;
  updated_at: string;
}

const UPLOAD_TIMEOUT_MS = 120000; // 2 minutes for large files

/**
 * Upload a PDF file for processing and indexing.
 */
export async function uploadPDF(file: File): Promise<UploadResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS);

  try {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch("/api/upload", {
      method: "POST",
      body: formData,
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    let data: Record<string, unknown>;
    try {
      data = await response.json();
    } catch {
      throw new Error("Invalid response from server");
    }

    if (!response.ok) {
      const errorMessage =
        (typeof data.detail === "string" ? data.detail : null) ||
        (typeof data.error === "string" ? data.error : null) ||
        `Upload failed (${response.status})`;
      throw new Error(errorMessage);
    }

    return {
      job_id: data.job_id as string,
      filename: data.filename as string,
      status: data.status as string,
      message: data.message as string,
    };
  } catch (err) {
    clearTimeout(timeoutId);

    if (err instanceof Error) {
      if (err.name === "AbortError") {
        throw new Error("Upload timed out. Please try again with a smaller file.");
      }
      throw err;
    }
    throw new Error("Upload failed");
  }
}

/**
 * Get the status of an upload job.
 */
export async function getUploadStatus(jobId: string): Promise<UploadStatus> {
  const response = await fetch(`/api/upload/status/${jobId}`);

  let data: Record<string, unknown>;
  try {
    data = await response.json();
  } catch {
    throw new Error("Invalid response from server");
  }

  if (!response.ok) {
    const errorMessage =
      (typeof data.detail === "string" ? data.detail : null) ||
      (typeof data.error === "string" ? data.error : null) ||
      `Failed to get status (${response.status})`;
    throw new Error(errorMessage);
  }

  return {
    job_id: data.job_id as string,
    filename: data.filename as string,
    status: data.status as UploadStatus["status"],
    progress: data.progress as number,
    chunks_created: data.chunks_created as number,
    error: data.error as string | undefined,
    created_at: data.created_at as string,
    updated_at: data.updated_at as string,
  };
}
