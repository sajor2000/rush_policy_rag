import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

export async function POST(request: NextRequest) {
  const startTime = Date.now();

  try {
    const body = await request.json();

    // Validate required fields
    if (!body.policy_ref || typeof body.policy_ref !== "string") {
      return NextResponse.json(
        { detail: "policy_ref is required" },
        { status: 400 }
      );
    }
    if (!body.search_term || typeof body.search_term !== "string") {
      return NextResponse.json(
        { detail: "search_term is required" },
        { status: 400 }
      );
    }

    // Forward to backend
    const backendResponse = await fetch(`${BACKEND_URL}/api/search-instances`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const data = await backendResponse.json();
    const elapsed = Date.now() - startTime;

    if (!backendResponse.ok) {
      console.error(
        `[Search Instances API] Backend error: ${backendResponse.status} (${elapsed}ms)`
      );
      return NextResponse.json(
        { detail: data.detail || "Search failed" },
        { status: backendResponse.status }
      );
    }

    console.info(
      `[Search Instances API] Success: ${data.total_instances} results for "${body.search_term}" in policy ${body.policy_ref} (${elapsed}ms)`
    );

    return NextResponse.json(data);
  } catch (error) {
    const elapsed = Date.now() - startTime;
    console.error(`[Search Instances API] Error (${elapsed}ms):`, error);

    return NextResponse.json(
      { detail: "Internal server error" },
      { status: 500 }
    );
  }
}
