import { NextRequest } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

/**
 * SSE Streaming Proxy Route
 *
 * Proxies Server-Sent Events from the FastAPI backend to the frontend.
 * This enables real-time streaming of chat responses for better perceived latency.
 *
 * Event types from backend:
 * - status: Pipeline progress updates ("Searching...", "Generating...")
 * - answer_chunk: Partial answer text as it's generated
 * - evidence: Evidence items array (sent once at end)
 * - sources: Source references (sent once at end)
 * - metadata: Response metadata (confidence, chunks_used, found)
 * - clarification: Device ambiguity clarification data
 * - done: End of stream marker
 * - error: Error during streaming
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { message } = body;

    if (!message || typeof message !== "string") {
      return new Response(
        formatSSEEvent("error", {
          type: "error",
          message: "Message is required",
        }),
        {
          status: 400,
          headers: {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            Connection: "keep-alive",
          },
        }
      );
    }

    // Forward request to FastAPI backend streaming endpoint
    const backendResponse = await fetch(`${BACKEND_URL}/api/chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message }),
    });

    if (!backendResponse.ok) {
      const errorData = await backendResponse.json().catch(() => ({}));
      const errorMessage =
        errorData.detail || `Backend error (${backendResponse.status})`;

      return new Response(
        formatSSEEvent("error", {
          type: "error",
          message: errorMessage,
        }),
        {
          status: backendResponse.status,
          headers: {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            Connection: "keep-alive",
          },
        }
      );
    }

    // Stream the response through
    const stream = new ReadableStream({
      async start(controller) {
        const reader = backendResponse.body?.getReader();
        if (!reader) {
          controller.enqueue(
            new TextEncoder().encode(
              formatSSEEvent("error", {
                type: "error",
                message: "No response body from backend",
              })
            )
          );
          controller.close();
          return;
        }

        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            controller.enqueue(value);
          }
        } catch (err) {
          const errorMsg =
            err instanceof Error ? err.message : "Stream read error";
          controller.enqueue(
            new TextEncoder().encode(
              formatSSEEvent("error", {
                type: "error",
                message: errorMsg,
              })
            )
          );
        } finally {
          controller.close();
        }
      },
    });

    return new Response(stream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no", // Disable nginx buffering
      },
    });
  } catch (err) {
    const errorMessage =
      err instanceof Error ? err.message : "Failed to process request";

    return new Response(
      formatSSEEvent("error", {
        type: "error",
        message: errorMessage,
      }),
      {
        status: 500,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        },
      }
    );
  }
}

/**
 * Format a Server-Sent Event string
 */
function formatSSEEvent(
  eventType: string,
  data: Record<string, unknown>
): string {
  return `event: ${eventType}\ndata: ${JSON.stringify(data)}\n\n`;
}
