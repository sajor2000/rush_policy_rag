# Rush Brand Style Guide for Coding Assistants

Use this guide when generating UI, documents, presentations, or any visual content for Rush University System for Health.

---

## Color Palette

### Primary Colors (Green Family)

| Name | Hex | RGB | Use Case |
|------|-----|-----|----------|
| **Legacy Green** | `#006332` | rgb(0, 99, 50) | Primary brand color, headers, buttons, logos |
| **Growth Green** | `#30AE6E` | rgb(48, 174, 110) | Secondary accents, highlights, success states |
| **Vitality Green** | `#5FEEA2` | rgb(95, 238, 162) | Light accents, backgrounds, hover states |
| **Sage Green** | `#DFF9EB` | rgb(223, 249, 235) | Backgrounds, cards, subtle highlights |

### Accent Colors

| Name | Hex | RGB | Use Case |
|------|-----|-----|----------|
| **Yellow** | `#FFC60B` | rgb(255, 198, 11) | Alerts, highlights, call-to-action accents |
| **Light Blue** | `#54ADD3` | rgb(84, 171, 211) | Links, informational elements |
| **Dark Blue** | `#005D83` | rgb(0, 93, 131) | Secondary headers, data viz |
| **Deep Purple** | `#2D1D4E` | rgb(46, 29, 78) | Dark mode accents, contrast elements |
| **Purple** | `#6C43B9` | rgb(108, 67, 185) | Highlights, badges |
| **Light Pink** | `#FFE3E0` | rgb(255, 227, 224) | Soft backgrounds, alerts |
| **Cream** | `#F2DBB3` | rgb(242, 219, 179) | Warm backgrounds |

### Neutral Colors

| Name | Hex | RGB | Use Case |
|------|-----|-----|----------|
| **Black** | `#000000` | rgb(0, 0, 0) | Text (sparingly) |
| **Charcoal** | `#5F5858` | rgb(95, 88, 88) | Body text, icons |
| **Gray** | `#AFAEAF` | rgb(175, 174, 175) | Borders, disabled states, secondary text |
| **Light Gray** | `#F5F5F5` | rgb(245, 245, 245) | Backgrounds |

---

## Typography

### Font Stack

```css
font-family: 'Calibre', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Droid Sans', 'Helvetica Neue', sans-serif;
```

### Font Weights

| Weight | Use Case |
|--------|----------|
| **Calibre Semibold** | Headings, buttons, emphasis |
| **Calibre Regular** | Body text, paragraphs |
| **Georgia Regular** | Formal documents, pull quotes (serif alternative) |

### Type Scale

| Element | Size | Weight |
|---------|------|--------|
| H1 | 3.125rem (50px) | Semibold |
| H2 | 1.875rem (30px) | Semibold |
| H3 | 1.5rem (24px) | Semibold |
| H4 | 1.25rem (20px) | Semibold |
| H5 | 1.125rem (18px) | Semibold |
| H6 | 1rem (16px) | Semibold |
| Lead/Intro | 1.5rem (24px) | Regular |
| Body | 1rem (16px) | Regular |

---

## CSS Variables Template

```css
:root {
  /* Primary Greens */
  --rush-legacy-green: #006332;
  --rush-growth-green: #30AE6E;
  --rush-vitality-green: #5FEEA2;
  --rush-sage-green: #DFF9EB;
  
  /* Accents */
  --rush-yellow: #FFC60B;
  --rush-light-blue: #54ADD3;
  --rush-dark-blue: #005D83;
  --rush-deep-purple: #2D1D4E;
  --rush-purple: #6C43B9;
  --rush-light-pink: #FFE3E0;
  --rush-cream: #F2DBB3;
  
  /* Neutrals */
  --rush-black: #000000;
  --rush-charcoal: #5F5858;
  --rush-gray: #AFAEAF;
  --rush-light-gray: #F5F5F5;
  
  /* Typography */
  --font-primary: 'Calibre', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-serif: 'Georgia', serif;
  
  /* Semantic Colors */
  --color-primary: var(--rush-legacy-green);
  --color-primary-light: var(--rush-growth-green);
  --color-background: var(--rush-sage-green);
  --color-text: var(--rush-charcoal);
  --color-text-muted: var(--rush-gray);
  --color-success: var(--rush-growth-green);
  --color-warning: var(--rush-yellow);
  --color-info: var(--rush-light-blue);
}
```

---

## Tailwind CSS Config

```javascript
// tailwind.config.js
module.exports = {
  theme: {
    extend: {
      colors: {
        rush: {
          'legacy': '#006332',
          'growth': '#30AE6E',
          'vitality': '#5FEEA2',
          'sage': '#DFF9EB',
          'yellow': '#FFC60B',
          'light-blue': '#54ADD3',
          'dark-blue': '#005D83',
          'deep-purple': '#2D1D4E',
          'purple': '#6C43B9',
          'light-pink': '#FFE3E0',
          'cream': '#F2DBB3',
          'charcoal': '#5F5858',
          'gray': '#AFAEAF',
        }
      },
      fontFamily: {
        'calibre': ['Calibre', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
        'georgia': ['Georgia', 'serif'],
      }
    }
  }
}
```

---

## Logo Usage

- **Clearspace**: Maintain padding equal to the height of the anchor icon on all sides
- **Minimum size**: Ensure legibility in print and digital
- **Approved color variants**:
  - Full color (Legacy Green `#006332`)
  - Single color black
  - White (reversed on dark backgrounds)
  - White on Legacy Green, Growth Green, or Dark Blue backgrounds

### Logo Don'ts
- ❌ Don't change logo colors arbitrarily
- ❌ Don't use patterns in the logo
- ❌ Don't skew or distort
- ❌ Don't crop or cover any part

---

## Brand Voice Guidelines

When generating text content, follow these personality traits:

### INCLUSIVE
- Real, genuine, thoughtful, collaborative
- Treat everyone as peers; avoid transactional language
- Person-first language: "patient with diabetes" not "diabetic patient"

### INVESTED
- All in, passionate, dedicated
- Focus on goals and outcomes
- Use momentum words: "let's," "next," "right now"
- Em-dashes for impactful conclusions

### INVENTIVE
- Confident, brave, optimistic, inspiring
- Show accomplishments, don't just claim them
- Short punchy statements + longer thoughtful ones
- Fragments welcome

### ACCESSIBLE
- Open, local, approachable
- Swap medical jargon for plain language
- Highlight benefits clearly
- Reference local community when relevant

---

## Component Examples

### Primary Button
```css
.btn-primary {
  background-color: #006332;
  color: white;
  font-family: 'Calibre', sans-serif;
  font-weight: 600;
  padding: 0.75rem 1.5rem;
  border-radius: 4px;
}
.btn-primary:hover {
  background-color: #30AE6E;
}
```

### Card
```css
.card {
  background-color: #DFF9EB;
  border-left: 4px solid #006332;
  padding: 1.5rem;
  border-radius: 4px;
}
```

### Alert/Highlight
```css
.alert-info {
  background-color: #54ADD3;
  color: white;
  padding: 1rem;
  border-radius: 4px;
}
.alert-warning {
  background-color: #FFC60B;
  color: #5F5858;
  padding: 1rem;
  border-radius: 4px;
}
```

---

## Quick Reference

| Element | Value |
|---------|-------|
| Primary Color | `#006332` |
| Secondary Color | `#30AE6E` |
| Background Light | `#DFF9EB` |
| Text Color | `#5F5858` |
| Font | Calibre / system sans-serif |
| Border Radius | 4px |

---

## Resources

- Brand portal: [brand.rush.edu](https://brand.rush.edu)
- Web style guide: [rushu.rush.edu/web-style-guide-overview](https://www.rushu.rush.edu/web-style-guide-overview)
