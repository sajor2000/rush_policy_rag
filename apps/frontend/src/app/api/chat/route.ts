import { NextRequest, NextResponse } from "next/server";
import { POLICYTECH_URL } from "@/lib/constants";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

// Validate BACKEND_URL at startup
function validateBackendUrl(): string {
  const url = BACKEND_URL;

  if (!url) {
    console.error(
      "[Chat API] BACKEND_URL is not configured. " +
      "Set BACKEND_URL environment variable (e.g., BACKEND_URL=http://localhost:8000)"
    );
    throw new Error("BACKEND_URL not configured");
  }

  try {
    new URL(url);
  } catch {
    console.error(
      `[Chat API] Invalid BACKEND_URL: '${url}'. ` +
      "Must be a valid URL (e.g., http://localhost:8000)"
    );
    throw new Error("Invalid BACKEND_URL format");
  }

  return url;
}

// Log levels
type LogLevel = "debug" | "info" | "warn" | "error";

function log(level: LogLevel, message: string, meta?: Record<string, unknown>) {
  const timestamp = new Date().toISOString();
  const metaStr = meta ? ` ${JSON.stringify(meta)}` : "";

  switch (level) {
    case "debug":
      if (process.env.LOG_LEVEL === "debug") {
        console.debug(`[${timestamp}] [DEBUG] [Chat API] ${message}${metaStr}`);
      }
      break;
    case "info":
      console.info(`[${timestamp}] [INFO] [Chat API] ${message}${metaStr}`);
      break;
    case "warn":
      console.warn(`[${timestamp}] [WARN] [Chat API] ${message}${metaStr}`);
      break;
    case "error":
      console.error(`[${timestamp}] [ERROR] [Chat API] ${message}${metaStr}`);
      break;
  }
}

export async function POST(request: NextRequest) {
  const startTime = Date.now();
  let messagePreview = "";

  try {
    // Validate backend URL
    const backendUrl = validateBackendUrl();

    const { message } = await request.json();
    messagePreview = message?.substring(0, 50) || "";

    if (!message || typeof message !== "string") {
      log("warn", "Invalid request: message missing or not a string");
      return NextResponse.json(
        { error: "Message is required" },
        { status: 400 }
      );
    }

    log("info", "Processing chat request", {
      messageLength: message.length,
      preview: messagePreview + (message.length > 50 ? "..." : "")
    });

    // Call the Python FastAPI backend
    const backendResponse = await fetch(`${backendUrl}/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message }),
    });

    const elapsed = Date.now() - startTime;

    if (!backendResponse.ok) {
      const errorData = await backendResponse.json().catch(() => ({}));

      log("error", "Backend returned error", {
        status: backendResponse.status,
        elapsed: `${elapsed}ms`,
        error: errorData.detail || "Unknown error"
      });

      return NextResponse.json(
        {
          error: errorData.detail || "Failed to get response from backend",
          response: `I'm having trouble connecting to the policy database right now. Please try again in a moment or contact Policy Administration at ${POLICYTECH_URL}`,
          summary: `I'm having trouble connecting to the policy database right now. Please try again in a moment or contact Policy Administration at ${POLICYTECH_URL}`,
          evidence: [],
          sources: [],
          found: false
        },
        { status: backendResponse.status }
      );
    }

    const data = await backendResponse.json();
    const totalElapsed = Date.now() - startTime;

    log("info", "Chat request completed", {
      elapsed: `${totalElapsed}ms`,
      chunksUsed: data.chunks_used || 0,
      evidenceCount: data.evidence?.length || 0,
      found: data.found ?? false
    });

    // Pass through the full structured response from the backend
    return NextResponse.json({
      response: data.response,
      summary: data.summary ?? data.response,
      evidence: data.evidence || [],
      sources: data.sources || [],
      raw_response: data.raw_response,
      chunks_used: data.chunks_used || 0,
      found: data.found ?? (data.evidence?.length > 0),
      confidence: data.confidence,
      clarification: data.clarification
    });
  } catch (error) {
    const elapsed = Date.now() - startTime;
    const errorMessage = error instanceof Error ? error.message : "Unknown error";

    log("error", "Chat request failed", {
      elapsed: `${elapsed}ms`,
      error: errorMessage,
      preview: messagePreview
    });

    // Return a helpful error message in RUSH voice
    return NextResponse.json(
      {
        error: "Failed to process request",
        response: `We're having trouble connecting right now. Let's try that again, or you can verify policies directly at ${POLICYTECH_URL}`,
        summary: `We're having trouble connecting right now. Let's try that again, or you can verify policies directly at ${POLICYTECH_URL}`,
        evidence: [],
        sources: [],
        found: false
      },
      { status: 500 }
    );
  }
}
