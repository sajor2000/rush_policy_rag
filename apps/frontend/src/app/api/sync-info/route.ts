import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

export async function GET() {
  try {
    const response = await fetch(`${BACKEND_URL}/api/sync-info`, {
      next: { revalidate: 300 }, // Cache for 5 minutes
    });

    if (!response.ok) {
      return NextResponse.json(
        {
          last_sync_date_display: "Unknown",
          last_sync_date: null,
          sync_batch_id: null,
        },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      {
        last_sync_date_display: "Unknown",
        last_sync_date: null,
        sync_batch_id: null,
      },
      { status: 502 }
    );
  }
}
