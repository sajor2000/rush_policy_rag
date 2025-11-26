# Rush University Policy Chat - Design Guidelines

## Brand Foundation

**Design System**: Rush University System for Health Brand Guidelines (provided)

**Brand Personality** (drives all design decisions):
- **ACCESSIBLE**: Approachable, real terms, drive home benefits
- **INCLUSIVE**: Real, genuine, thoughtful, collaborative, peer-level
- **INVESTED**: Momentum-driven language, urgent, dedicated
- **INVENTIVE**: Confident, optimistic, show don't tell

## Color Palette

**Primary Colors**:
- Legacy Green: `#006332` (primary brand color, CTAs, headers)
- Growth Green: `#30AE6E` (secondary actions, accents)
- Vitality Green: `#5FEEA2` (highlights, success states)
- Sage Green: `#DFF9EB` (backgrounds, subtle sections)

**Supporting Colors**:
- Rush Blue: `#54ADD3` (supporting accents)
- Rush Purple: `#6C43B9` (supporting accents)

**Usage**: Use Legacy Green for primary actions and brand presence. Growth/Vitality for interactive elements. Sage for calm background sections. Maintain WCAG 2.1 AA contrast ratios.

## Typography

**Font Families**:
- Headings: Calibre Semibold
- Body: Calibre Regular  
- Emphasis: Georgia Regular (italic)

**Hierarchy**:
- H1: 32-48px (Calibre Semibold)
- H2: 24-32px (Calibre Semibold)
- Body: 16-18px (Calibre Regular)
- Emphasis/Quotes: Georgia Regular italic

**Implementation**: Use @font-face for Calibre with system font fallbacks (Inter, -apple-system, sans-serif). Georgia is system-available.

## Layout & Spacing

**Spacing System**: Use Tailwind units of 4, 6, 8, 12, 16, 24 for consistent rhythm (e.g., p-4, gap-6, mb-8)

**Container Strategy**:
- Hero: Full-width with max-w-5xl inner content
- Chat Interface: max-w-4xl centered
- Content sections: max-w-6xl

**Component Structure**:
1. **Hero Section**: Centered content, Rush Legacy Green heading, collaborative copy ("Policy answers, instantly. Because your time matters."), CTA button with Legacy Green background
2. **Prompting Tips**: Card-based layout, 2-column on desktop (single on mobile), collaborative examples with Rush voice
3. **Chat Interface**: Full-width message area, user messages aligned right with Growth Green background, AI responses left-aligned with subtle Sage background, input field with Legacy Green focus state
4. **Logo Header**: Rush logo (green on white), proper clearspace maintained

## Voice & Copy Guidelines

**Writing Style**:
- Short punchy statements OK
- Fragments welcome  
- Use em-dashes—for impact
- Collaborative not transactional
- "Let's" language over "You" commands

**Copy Examples**:
- Hero: "Policy answers, instantly. Because your time matters."
- CTA: "Let's get started" (not "Begin search")
- Tips: "Try asking: 'What's the visitor policy for ICU?'"
- Errors: "We're having trouble connecting right now. Let's try that again."

## Component Specifications

**Hero Section**:
- No large background image needed (clean, focused)
- Rush Legacy Green heading text
- Calibre Semibold for headline
- White/light background
- Clear CTA button (Legacy Green, rounded corners)

**Chat Interface**:
- Clean spacing with generous padding (p-6 to p-8)
- Message bubbles: rounded-lg, subtle shadows
- User messages: Growth Green background, white text, right-aligned
- AI responses: White/Sage background, dark text, left-aligned
- Input field: Border in Legacy Green on focus, placeholder in collaborative voice
- Markdown support for policy citations (bold, lists, links in Rush green)

**Prompting Tips**:
- Grid layout: grid-cols-1 md:grid-cols-2, gap-6
- Each tip card: white background, subtle border, p-6
- Examples in conversational tone
- Icons optional (can use simple green bullet points)

**Logo Integration**:
- Top left or center header
- Green version on white background
- Maintain 2x clearspace minimum
- SVG format, accessible alt text

## Accessibility Requirements

- WCAG 2.1 AA compliant
- Color contrast ratios verified (especially green text on white)
- Keyboard navigation for all interactive elements
- Focus states visible (Rush green outline)
- Screen reader friendly labels
- Proper heading hierarchy (H1 → H2 → H3)
- Skip to content link

## Responsive Behavior

**Mobile-First Approach**:
- Base: 320px-640px (single column, stacked layout)
- Tablet: 641px-1024px (2-column for tips)
- Desktop: 1025px+ (max-width containers, generous spacing)

**Chat Interface**: Full-width on mobile with 16px padding, expands to max-w-4xl on desktop

## Loading & Error States

**Loading**: Animated dots or pulse in Rush Growth Green, copy: "Finding answers..."

**Errors**: Empathetic Rush voice, Vitality Green for retry button, copy emphasizes collaboration ("Let's try that again")

**Empty State**: Prompting tips visible, welcoming collaborative language

## Images

**No hero image required** - this is a utility application focused on clean, accessible chat functionality. Visual brand presence comes from Rush green color palette, logo, and typography rather than photography.