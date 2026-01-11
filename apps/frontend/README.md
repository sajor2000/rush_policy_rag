# RUSH Policy RAG Frontend

Next.js 14 frontend for the RUSH Policy RAG system. Features App Router, Server Components, and RUSH brand styling.

## Quick Start

```bash
cd apps/frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## Tech Stack

- **Framework**: Next.js 14 with App Router
- **Language**: TypeScript
- **Styling**: Tailwind CSS with RUSH brand colors
- **UI Components**: shadcn/ui (Radix primitives)
- **Icons**: Lucide React

## Project Structure

```
src/
├── app/                    # Next.js App Router
│   ├── layout.tsx          # Root layout with providers
│   ├── page.tsx            # Main chat page
│   ├── providers.tsx       # Context providers
│   └── api/                # API route handlers (proxy to backend)
│       ├── chat/route.ts           # POST /api/chat
│       ├── chat/stream/route.ts    # POST /api/chat/stream (SSE)
│       ├── health/route.ts         # GET /api/health
│       ├── pdf/[...filename]/route.ts  # GET /api/pdf/{filename}
│       └── search-instances/route.ts   # POST /api/search-instances
├── components/             # React components
│   ├── ChatInterface.tsx   # Main chat container
│   ├── ChatMessage.tsx     # Message rendering with citations
│   ├── PDFViewer.tsx       # PDF display modal
│   ├── InstanceSearchModal.tsx  # Within-policy search
│   ├── HeroSection.tsx     # Landing section
│   ├── PromptingTips.tsx   # Usage tips
│   ├── chat/               # Chat-specific components
│   │   └── FormattedQuickAnswer.tsx
│   └── ui/                 # shadcn/ui primitives
├── lib/                    # Utilities
│   ├── api.ts              # Backend API client
│   ├── chatMessageFormatting.ts  # Citation parsing
│   ├── constants.ts        # App constants
│   └── utils.ts            # Tailwind cn() helper
└── hooks/                  # React hooks
    ├── use-mobile.tsx      # Mobile detection
    └── use-toast.ts        # Toast notifications
```

## Key Components

| Component | Purpose |
|-----------|---------|
| `ChatInterface.tsx` | Main chat container with message state, PDF viewer, instance search |
| `ChatMessage.tsx` | Renders messages with evidence, sources, and citations |
| `PDFViewer.tsx` | Modal for viewing policy PDFs with SAS URL |
| `InstanceSearchModal.tsx` | Search within a specific policy document |
| `HeroSection.tsx` | Landing page hero with CTA |
| `PromptingTips.tsx` | Example queries and usage tips |

## Environment Variables

Create `.env.local`:

```bash
# Backend API URL (required)
BACKEND_URL=http://localhost:8000

# Optional: Client-side API URL (defaults to relative /api)
# NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Available Scripts

```bash
npm run dev      # Development server (port 3000)
npm run build    # Production build
npm start        # Start production server
npm run check    # TypeScript type checking
npm run lint     # ESLint
```

## RUSH Brand Colors

Configured in `tailwind.config.ts`:

| Color | Hex | Usage |
|-------|-----|-------|
| Legacy Green | `#006332` | Primary brand, CTAs, headers |
| Growth Green | `#30AE6E` | Secondary actions, user messages |
| Vitality Green | `#5FEEA2` | Highlights, success states |
| Sage Green | `#DFF9EB` | Backgrounds, AI messages |
| Rush Blue | `#54ADD3` | Supporting accents |
| Rush Purple | `#6C43B9` | Supporting accents |

## Security Headers

Configured in `next.config.js`:

- **Content-Security-Policy**: Restricts script sources
- **X-Frame-Options**: DENY (prevents clickjacking)
- **X-Content-Type-Options**: nosniff
- **Strict-Transport-Security**: HSTS enabled
- **Referrer-Policy**: strict-origin-when-cross-origin

## API Proxy Routes

The frontend proxies API requests to the backend:

| Frontend Route | Backend Route | Purpose |
|----------------|---------------|---------|
| `POST /api/chat` | `POST /api/chat` | Chat message |
| `POST /api/chat/stream` | `POST /api/chat/stream` | Streaming SSE |
| `GET /api/health` | `GET /health` | Health check |
| `GET /api/pdf/{filename}` | `GET /api/pdf/{filename}` | PDF SAS URL |
| `POST /api/search-instances` | `POST /api/search-instances` | Instance search |

## Development

### Adding New Components

1. Create component in `src/components/`
2. Use shadcn/ui primitives from `src/components/ui/`
3. Follow RUSH brand guidelines in `design_guidelines.md`
4. Use `cn()` from `src/lib/utils.ts` for conditional classes

### TypeScript Path Alias

`@/*` maps to `./src/*`:

```typescript
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
```

### Adding shadcn/ui Components

```bash
npx shadcn@latest add button
npx shadcn@latest add dialog
```

## Accessibility

- WCAG 2.1 AA compliance required
- Maintain contrast ratios with RUSH colors
- Keyboard navigation support
- Screen reader compatible

## Production Build

```bash
npm run build
npm start
```

Or with Docker:

```bash
docker build -t rush-policy-frontend .
docker run -p 3000:3000 -e BACKEND_URL=http://backend:8000 rush-policy-frontend
```

## Troubleshooting

### Backend Connection Issues

- Verify `BACKEND_URL` is set correctly
- Check backend is running on expected port
- Ensure CORS allows frontend origin

### Build Errors

- Run `npm run check` for TypeScript errors
- Clear `.next/` cache: `rm -rf .next`
- Reinstall dependencies: `rm -rf node_modules && npm install`

### PDF Viewing Issues

- PDFs must be uploaded to Azure Blob Storage
- Backend must have `STORAGE_CONNECTION_STRING` configured
- Check browser console for CORS errors

## Design Guidelines

See `design_guidelines.md` for complete RUSH brand specifications including:
- Color palette
- Typography (Calibre, Georgia)
- Component specifications
- Voice and copy guidelines
