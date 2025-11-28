import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

/**
 * Proxy PDF upload to the FastAPI backend.
 * Handles multipart/form-data file uploads.
 */
export async function POST(request: NextRequest) {
  const startTime = Date.now();

  try {
    // Get the form data from the request
    const formData = await request.formData();
    const file = formData.get("file") as File | null;

    if (!file) {
      return NextResponse.json(
        { error: "No file provided" },
        { status: 400 }
      );
    }

    console.log(`[Upload API] Uploading file: ${file.name} (${file.size} bytes)`);

    // Forward to backend
    const backendFormData = new FormData();
    backendFormData.append("file", file);

    const backendResponse = await fetch(`${BACKEND_URL}/api/upload`, {
      method: "POST",
      body: backendFormData,
    });

    const elapsed = Date.now() - startTime;

    if (!backendResponse.ok) {
      const errorData = await backendResponse.json().catch(() => ({}));
      console.error(`[Upload API] Backend error: ${backendResponse.status}`, errorData);

      return NextResponse.json(
        {
          error: errorData.detail || "Upload failed",
          message: errorData.message || "Failed to upload file to server"
        },
        { status: backendResponse.status }
      );
    }

    const data = await backendResponse.json();
    console.log(`[Upload API] Upload successful in ${elapsed}ms: job_id=${data.job_id}`);

    return NextResponse.json(data);
  } catch (error) {
    const elapsed = Date.now() - startTime;
    console.error(`[Upload API] Error after ${elapsed}ms:`, error);

    return NextResponse.json(
      {
        error: "Upload failed",
        message: error instanceof Error ? error.message : "An unexpected error occurred"
      },
      { status: 500 }
    );
  }
}

/**
 * List recent upload jobs.
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const limit = searchParams.get("limit") || "20";

    const backendResponse = await fetch(
      `${BACKEND_URL}/api/upload/jobs?limit=${limit}`,
      { method: "GET" }
    );

    if (!backendResponse.ok) {
      const errorData = await backendResponse.json().catch(() => ({}));
      return NextResponse.json(
        { error: errorData.detail || "Failed to fetch jobs" },
        { status: backendResponse.status }
      );
    }

    const data = await backendResponse.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("[Upload API] Error fetching jobs:", error);
    return NextResponse.json(
      { error: "Failed to fetch upload jobs" },
      { status: 500 }
    );
  }
}
