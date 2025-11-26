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
const REQUEST_TIMEOUT_MS = 30000;
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1000;

/**
 * Send a chat message with timeout, retry, and validation.
 */
export async function sendMessage(message: string): Promise<ChatApiResponse> {
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

        // Retry on 5xx errors
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
