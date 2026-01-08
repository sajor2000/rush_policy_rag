# Staging Test Checklist - Melissa Feedback Release

**Release**: 2026-01-08
**Tester**: _______________
**Date**: _______________
**Environment**: Staging / Production (circle one)

---

## Pre-Deployment Tests (Local)

### Backend Unit Tests

- [ ] All 71 tests passing
  ```bash
  cd apps/backend && python3 -m pytest tests/ -v --tb=short
  ```
  - [ ] 3 auth tests
  - [ ] 14 chat service tests (including 8 new ambiguity tests, 6 new score windowing tests)
  - [ ] 2 citation formatter tests
  - [ ] 24 security tests
  - [ ] 28 synonym service tests (including 9 new expansion tests)

### Frontend Build

- [ ] TypeScript check passes
  ```bash
  cd apps/frontend && npm run check
  ```
- [ ] Production build succeeds
  ```bash
  npm run build
  ```

### Azure Synonym Map

- [ ] Updated synonym map uploaded
  ```bash
  cd apps/backend && python azure_policy_index.py synonyms
  ```
- [ ] Synonym test passes
  ```bash
  python azure_policy_index.py test-synonyms "peripheral IV dwell time"
  ```

---

## Deployment Verification

### Health Checks

- [ ] Backend health endpoint responds
  ```bash
  curl https://rush-policy-backend-staging.azurecontainerapps.io/health
  ```
  - Expected: `{"status": "healthy", ...}`

- [ ] Frontend loads
  - Open: https://rush-policy-frontend-staging.azurecontainerapps.io
  - Expected: Chat interface visible

### API Connectivity

- [ ] Backend → Azure OpenAI connection
- [ ] Backend → Cohere Rerank connection
- [ ] Backend → Azure AI Search connection
- [ ] Backend → Azure Blob Storage connection
- [ ] Frontend → Backend API connection

---

## Feature 1: Device Ambiguity Detection

### Test Case 1.1: Ambiguous "IV" Query

- [ ] **Input**: "how long can an IV stay in place"
- [ ] **Expected**: Clarification prompt appears (amber background)
- [ ] **Expected**: 4 options displayed:
  - [ ] "Peripheral IV (short-term, 72-96 hours)"
  - [ ] "PICC line (long-term central line)"
  - [ ] "Central venous catheter (CVC, triple lumen)"
  - [ ] "Any IV or catheter (show all results)"
- [ ] **Action**: Click "Peripheral IV"
- [ ] **Expected**: Query refined automatically, answer returned
- [ ] **Verify**: Evidence contains ONLY peripheral IV policies
- [ ] **Verify**: NO PICC line policies in results
- [ ] **Verify**: NO epidural catheter policies in results

**Notes**: _______________________________________________________________

### Test Case 1.2: Clear "Peripheral IV" Query

- [ ] **Input**: "peripheral IV dwell time"
- [ ] **Expected**: NO clarification prompt
- [ ] **Expected**: Direct answer with peripheral IV policies
- [ ] **Verify**: Response has `confidence: "medium"` or `"high"` (not `"clarification_needed"`)

**Notes**: _______________________________________________________________

### Test Case 1.3: Ambiguous "Catheter" Query

- [ ] **Input**: "catheter care procedures"
- [ ] **Expected**: Clarification prompt appears
- [ ] **Expected**: 4 options:
  - [ ] "Urinary catheter (Foley)"
  - [ ] "IV catheter (peripheral or central)"
  - [ ] "Epidural catheter"
  - [ ] "Any catheter (show all results)"

**Notes**: _______________________________________________________________

### Test Case 1.4: Clear "Foley" Query

- [ ] **Input**: "Foley catheter removal"
- [ ] **Expected**: NO clarification prompt
- [ ] **Expected**: Direct answer with urinary catheter policies

**Notes**: _______________________________________________________________

### Test Case 1.5: Ambiguous "Line" Query

- [ ] **Input**: "line dressing change frequency"
- [ ] **Expected**: Clarification prompt appears
- [ ] **Expected**: 3-4 options (Peripheral/Central/Arterial/All)

**Notes**: _______________________________________________________________

### Test Case 1.6: Ambiguous "Port" Query

- [ ] **Input**: "how to access a port"
- [ ] **Expected**: Clarification prompt appears
- [ ] **Expected**: 2 options (Implanted port / Dialysis port)

**Notes**: _______________________________________________________________

### Test Case 1.7: Non-Device Query

- [ ] **Input**: "hand hygiene policy"
- [ ] **Expected**: NO clarification prompt
- [ ] **Expected**: Direct answer

**Notes**: _______________________________________________________________

---

## Feature 2: Three-Tier PDF Access

### Test Case 2.1: Per-Evidence PDF Button (Tier 1)

- [ ] **Setup**: Ask any question that returns evidence with PDFs
  - Suggested: "peripheral IV insertion procedure"
- [ ] **Verify**: Each evidence card header has a green "PDF" button
- [ ] **Action**: Click PDF button on evidence card #1
- [ ] **Expected**: PDF viewer opens
- [ ] **Expected**: PDF navigates to correct page (if `page_number` available)
- [ ] **Verify**: PDF filename matches evidence `source_file`
- [ ] **Verify**: PDF title displays correctly

**Notes**: _______________________________________________________________

### Test Case 2.2: Sticky Quick Access Panel (Tier 2)

- [ ] **Setup**: Ask question with multiple evidence sources
- [ ] **Verify**: Sticky panel appears at top with heading "Quick Access: Source PDFs"
- [ ] **Verify**: Panel shows numbered buttons (1, 2, 3...)
- [ ] **Action**: Scroll down through evidence cards
- [ ] **Expected**: Sticky panel remains visible at top
- [ ] **Action**: Click numbered PDF button (e.g., button "2")
- [ ] **Expected**: PDF viewer opens for source #2
- [ ] **Verify**: Button text truncates long filenames with "..."

**Notes**: _______________________________________________________________

### Test Case 2.3: Bottom Section (Tier 3 - Fallback)

- [ ] **Action**: Scroll to bottom of answer
- [ ] **Verify**: "View Source PDFs" section still present
- [ ] **Action**: Click PDF link in bottom section
- [ ] **Expected**: PDF viewer opens (same behavior as tiers 1 & 2)

**Notes**: _______________________________________________________________

### Test Case 2.4: PDF Button Accessibility

- [ ] **Action**: Tab through evidence cards using keyboard
- [ ] **Verify**: PDF buttons are keyboard-focusable
- [ ] **Action**: Press Enter on focused PDF button
- [ ] **Expected**: PDF viewer opens
- [ ] **Verify**: ARIA labels present (`aria-label="View source PDF"`)

**Notes**: _______________________________________________________________

---

## Feature 3: Context-Aware Synonym Expansion

### Test Case 3.1: "IV" Neutral Fallback (No Cascade)

- [ ] **Backend Test**: Verify expansion doesn't cascade
  ```python
  from app.services.synonym_service import get_synonym_service
  service = get_synonym_service()
  result = service.expand_query("how long can an IV stay in place")
  expanded = result.expanded_query.lower()

  # Should contain neutral terms
  assert 'intravenous' in expanded or 'vascular' in expanded

  # Should NOT cascade to urinary catheter terms
  assert 'foley' not in expanded
  assert 'urinary' not in expanded
  ```
- [ ] **Expected**: Expansion contains "intravenous" or "vascular"
- [ ] **Expected**: NO "foley", "urinary", "bladder" terms

**Notes**: _______________________________________________________________

### Test Case 3.2: "Peripheral IV" Specific Expansion

- [ ] **Backend Test**: Verify device-specific expansion
  ```python
  result = service.expand_query("peripheral IV dwell time")
  expanded = result.expanded_query.lower()

  # Should contain specific peripheral IV terms
  assert 'piv' in expanded or 'short-term' in expanded

  # Should NOT cascade to PICC or central line
  assert 'picc' not in expanded
  ```
- [ ] **Expected**: Contains "PIV" or "short-term"
- [ ] **Expected**: NO "PICC", "central venous catheter"

**Notes**: _______________________________________________________________

---

## Feature 4: Score Windowing

### Test Case 4.1: Filters Noise After Clarification

- [ ] **Input**: "IV dwell time" → Select "Peripheral IV"
- [ ] **Verify**: Results are tightly clustered (2-4 policies)
- [ ] **Verify**: NO PICC line policies in results
- [ ] **Verify**: NO epidural catheter policies in results
- [ ] **Check Backend Logs**: Look for "Score windowing: X → Y results"
- [ ] **Expected**: Log shows filtering occurred (e.g., "8 → 3 results")

**Notes**: _______________________________________________________________

### Test Case 4.2: Preserves Tight Clusters

- [ ] **Input**: Query with naturally tight results (e.g., "hand hygiene")
- [ ] **Verify**: All relevant results retained (no over-filtering)
- [ ] **Check Backend Logs**: Should show "skipping score windowing" or minimal filtering

**Notes**: _______________________________________________________________

---

## Feature 5: Collapsible "Related" Evidence

### Test Case 5.1: Related Evidence Collapsed by Default

- [ ] **Setup**: Find a response with "related" evidence
  - Look for evidence with gray badge instead of green
- [ ] **Verify**: Evidence card has gray badge "(Related Evidence)"
- [ ] **Verify**: Content is collapsed (not visible by default)
- [ ] **Verify**: Shows "Show related evidence" expandable section
- [ ] **Verify**: Warning text present: "(may not directly support the answer)"

**Notes**: _______________________________________________________________

### Test Case 5.2: Expand Related Evidence

- [ ] **Action**: Click "Show related evidence"
- [ ] **Expected**: Content expands and becomes visible
- [ ] **Expected**: Chevron icon rotates 90 degrees
- [ ] **Verify**: Content formatting is correct (no overflow)

**Notes**: _______________________________________________________________

### Test Case 5.3: Cited Evidence NOT Collapsed

- [ ] **Verify**: Evidence with green badge "✓ Cited in Answer" is NOT collapsed
- [ ] **Verify**: Cited evidence shows content by default

**Notes**: _______________________________________________________________

---

## Regression Testing

### Existing Features (Must Still Work)

- [ ] **Search Functionality**: Basic search returns results
- [ ] **Evidence Display**: Citations, snippets, metadata display correctly
- [ ] **PDF Viewing**: PDF viewer modal works
- [ ] **Copy Button**: Copy snippet button on evidence cards works
- [ ] **Metadata Fields**: Title, reference number, section, date visible
- [ ] **Entity Filtering**: "Applies To" tags display correctly
- [ ] **Mobile Responsive**: UI works on mobile viewport (test at 375px width)
- [ ] **Error Handling**: Invalid queries show appropriate error messages
- [ ] **Loading States**: Spinner shows during API calls
- [ ] **Empty States**: "No results found" message displays when appropriate

**Notes**: _______________________________________________________________

---

## Performance Testing

### Response Times

- [ ] **Query with Clarification**: < 2 seconds for clarification prompt
- [ ] **Query with Direct Answer**: < 5 seconds for full response
- [ ] **PDF Button Click**: < 1 second to open PDF viewer
- [ ] **Sticky Panel Scroll**: Smooth scrolling (no jank)

**Notes**: _______________________________________________________________

### Resource Usage

- [ ] **Backend CPU**: Normal range (check Azure metrics)
- [ ] **Backend Memory**: No memory leaks (check over 10 queries)
- [ ] **Frontend Bundle Size**: No significant increase
- [ ] **Network Requests**: Efficient (no duplicate API calls)

**Notes**: _______________________________________________________________

---

## Edge Cases and Error Handling

### Edge Case 1: Empty Results After Clarification

- [ ] **Setup**: Create a scenario with no matching policies after clarification
- [ ] **Expected**: "No results found" message
- [ ] **Expected**: No crash or blank screen

**Notes**: _______________________________________________________________

### Edge Case 2: Multiple Ambiguous Terms

- [ ] **Input**: "IV line catheter care"
- [ ] **Expected**: Only ONE clarification prompt (for first detected term)
- [ ] **Verify**: No cascading clarification prompts

**Notes**: _______________________________________________________________

### Edge Case 3: PDF Missing from Blob Storage

- [ ] **Setup**: Evidence references PDF that doesn't exist in blob storage
- [ ] **Action**: Click PDF button
- [ ] **Expected**: Graceful error message (not 404 crash)

**Notes**: _______________________________________________________________

### Edge Case 4: Very Long Policy Title

- [ ] **Verify**: Sticky panel button text truncates correctly
- [ ] **Verify**: No horizontal overflow on mobile

**Notes**: _______________________________________________________________

---

## Browser Compatibility

Test on:

- [ ] **Chrome**: Latest version
- [ ] **Safari**: Latest version
- [ ] **Firefox**: Latest version
- [ ] **Edge**: Latest version
- [ ] **Mobile Safari (iOS)**: Latest version
- [ ] **Mobile Chrome (Android)**: Latest version

**Notes**: _______________________________________________________________

---

## Accessibility (WCAG 2.1 AA)

- [ ] **Keyboard Navigation**: All interactive elements reachable via Tab
- [ ] **Screen Reader**: ARIA labels present on PDF buttons
- [ ] **Color Contrast**: All text meets 4.5:1 contrast ratio
- [ ] **Focus Indicators**: Visible focus rings on all interactive elements
- [ ] **Semantic HTML**: Proper heading hierarchy (h1 → h2 → h3)

**Notes**: _______________________________________________________________

---

## Final Sign-Off

### Testing Summary

- **Total Test Cases**: 40+
- **Passed**: _____ / _____
- **Failed**: _____ (list below)
- **Blocked**: _____ (list below)

### Failed Test Cases

1. _________________________________________________________________
2. _________________________________________________________________
3. _________________________________________________________________

### Blocked Test Cases

1. _________________________________________________________________
2. _________________________________________________________________

### Recommendation

- [ ] **APPROVE** for production deployment
- [ ] **REJECT** - critical issues found (list above)
- [ ] **CONDITIONAL APPROVAL** - minor issues, proceed with monitoring

**Tester Signature**: _______________  **Date**: _______________

**Reviewer Signature**: _______________  **Date**: _______________

---

## Post-Deployment Monitoring (First 24 Hours)

- [ ] **Hour 1**: Check error logs for spikes
- [ ] **Hour 4**: Verify clarification trigger rate (target: 5-10%)
- [ ] **Hour 8**: Review user feedback (if available)
- [ ] **Hour 24**: Confirm no critical issues reported

**Notes**: _______________________________________________________________

---

**Document Version**: 1.0
**Last Updated**: 2026-01-08
