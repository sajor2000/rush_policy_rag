import { NextResponse } from 'next/server';

/**
 * Health check endpoint for container orchestration.
 * Required by Docker HEALTHCHECK and Azure Container Apps.
 */
export async function GET() {
  return NextResponse.json({
    status: 'ok',
    timestamp: new Date().toISOString(),
  });
}
