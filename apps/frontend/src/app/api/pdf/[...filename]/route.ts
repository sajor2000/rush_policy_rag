import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ filename: string[] }> }
) {
  try {
    const { filename } = await params;
    const pdfFilename = filename.join("/");

    if (!pdfFilename.endsWith(".pdf")) {
      return NextResponse.json(
        { error: "Only PDF files are supported" },
        { status: 400 }
      );
    }

    // Don't re-encode - pdfFilename is already URL-decoded by Next.js
    // Re-encoding would cause double-encoding (spaces become %2520 instead of %20)
    const backendResponse = await fetch(
      `${BACKEND_URL}/api/pdf/${pdfFilename}`,
      { method: "GET" }
    );

    if (!backendResponse.ok) {
      const errorData = await backendResponse.json().catch(() => ({}));
      return NextResponse.json(
        { error: errorData.detail || "Failed to get PDF URL" },
        { status: backendResponse.status }
      );
    }

    const data = await backendResponse.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("PDF API error:", error);
    return NextResponse.json(
      { error: "Failed to process PDF request" },
      { status: 500 }
    );
  }
}
