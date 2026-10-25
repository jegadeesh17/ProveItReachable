---
name: ProveItReachable
description: A reachability assay printed as a clinical lab report; evidence before explanation, uncertainty as a normal printed result.
colors:
  lab-white: "#f7f8fa"
  report-sheet: "#fcfdfe"
  assay-ink: "#20252b"
  ink-secondary: "#444e59"
  ink-tertiary: "#596470"
  hairline: "#d3d9e0"
  control-edge: "#747f8b"
  disabled-fill: "#e6e9ed"
  report-blue: "#2f6fb0"
  report-blue-deep: "#1f5488"
  report-blue-tint: "#e4edf7"
  report-blue-tint-strong: "#cfe0f2"
  flag-red: "#c2362b"
  flag-red-tint: "#f9e8e6"
  flag-amber: "#e3b341"
  flag-amber-tint: "#fbf1d6"
typography:
  display:
    fontFamily: "Public Sans, system-ui, Segoe UI, Helvetica Neue, sans-serif"
    fontSize: "clamp(3.052rem, 2rem + 5vw, 5.5rem)"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.04em"
    fontFeature: "tnum"
  headline:
    fontFamily: "Public Sans, system-ui, Segoe UI, Helvetica Neue, sans-serif"
    fontSize: "1.5625rem"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Public Sans, system-ui, Segoe UI, Helvetica Neue, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.35
  body:
    fontFamily: "Public Sans, system-ui, Segoe UI, Helvetica Neue, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  label:
    fontFamily: "Public Sans, system-ui, Segoe UI, Helvetica Neue, sans-serif"
    fontSize: "0.8rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0.08em"
  data:
    fontFamily: "JetBrains Mono, ui-monospace, Cascadia Mono, SF Mono, Consolas, monospace"
    fontSize: "0.9em"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  control: "2px"
  round: "50%"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  xxl: "48px"
  page: "64px"
components:
  button-primary:
    backgroundColor: "{colors.report-blue}"
    textColor: "{colors.report-sheet}"
    rounded: "{rounded.control}"
    padding: "8px 24px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.report-blue-deep}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.report-blue-deep}"
    rounded: "{rounded.control}"
    padding: "8px 24px"
    height: "44px"
  button-secondary-hover:
    backgroundColor: "{colors.report-blue-tint}"
  input-field:
    backgroundColor: "{colors.report-sheet}"
    textColor: "{colors.assay-ink}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
    height: "48px"
  specimen-block:
    backgroundColor: "{colors.report-sheet}"
    textColor: "{colors.assay-ink}"
    rounded: "{rounded.control}"
    padding: "16px 24px"
  result-row-reach:
    backgroundColor: "{colors.flag-red-tint}"
    textColor: "{colors.assay-ink}"
    padding: "16px"
  result-row-uncertain:
    backgroundColor: "{colors.flag-amber-tint}"
    textColor: "{colors.assay-ink}"
    padding: "16px"
---

# Design System: ProveItReachable

## Overview

**Creative North Star: "The Assay Result Sheet"**

Every scan is issued as a clinical lab report. The repository is the specimen, each advisory is a test, and the verdict sits in a flag column the way a result sits against a reference range. `uncertain` is a normal printed result, not an error state. The system refuses the category defaults of stat tiles, coloured severity badges and a wall of cards: structure comes from hairline rules, a boxed specimen header, a ruled results list, footnotes and a sign-off line.

The mood is precise, flat and quiet. Paper is cool lab white, ink is near-black, and the report blue draws structure, rules and links. Red and amber are not decoration: they are reserved for the two verdicts that need a human (`likely reachable`, `uncertain`) and appear only inside a flag mark or its row tint. Everything is operate-mode dense: a reader should find the flag, the package, the advisory id and the file:line without opening anything.

Evidence comes before explanation, everywhere. In every finding the vulnerable function, call path, source locations and advisory links precede confidence, limitations and the model's explanation.

**Key Characteristics:**
- Flat, tonal and ruled; no shadows, no gradients, no blur.
- One workhorse sans with tabular figures, one mono for data (file:line, SHAs, advisory ids).
- Verdicts are always shape plus words; colour only reinforces.
- Squared controls (2px), 1px hairlines, 1.5px structural rules, a 4px double rule under the letterhead.
- Single light theme.

## Colors

A cool-tinted neutral ramp, one structural blue, and two reserved flag colours.

### Primary
- **Report Blue** (#2f6fb0): structure, rules, links, the primary button, done-stage stamps. Used on well under a tenth of any screen.
- **Report Blue Deep** (#1f5488): hover, focus ring, and text on blue tints (4.5:1 or better).

### Secondary
- **Flag Red** (#c2362b): reserved for `likely reachable`. Only the filled triangle, the sink step of a call path and the red underline of the "likely reachable" numeral.
- **Flag Amber** (#e3b341): reserved for `uncertain`. Only the hatched mark, the uncertain row tint and the "why this is uncertain" box. Never used as text.

### Neutral
- **Lab White** (#f7f8fa): page ground.
- **Report Sheet** (#fcfdfe): raised surfaces: specimen block, finding body, inputs.
- **Assay Ink** (#20252b): text, strong rules, outlines of marks.
- **Ink Secondary** (#444e59) and **Ink Tertiary** (#596470): secondary and quiet text; both clear 4.5:1 on Lab White.
- **Hairline** (#d3d9e0): row dividers. **Control Edge** (#747f8b): input and stamp boundaries (3:1).
- **Flag Red Tint** (#f9e8e6), **Flag Amber Tint** (#fbf1d6), **Report Blue Tint** (#e4edf7): row and hover fills; text on them is always Assay Ink or Report Blue Deep.

### Named Rules
**The Reserved Flags Rule.** Red means `likely reachable` and nothing else; amber means `uncertain` and nothing else. No error, warning or success state borrows them. Errors and notices use ink outlines and a drawn icon.
**The Never Colour Alone Rule.** Every verdict is a mark (filled triangle, hatched square, outline dash) plus its exact words. Amber alone fails 3:1 on paper, so the hatched mark always carries an ink outline.

## Typography

**Display and body font:** Public Sans (with system-ui, Segoe UI, Helvetica Neue)
**Data font:** JetBrains Mono (with ui-monospace, Cascadia Mono, Consolas)

**Character:** a neutral, legible institutional sans carries all prose and numerals; mono is used only for things a developer would copy: file:line, commit SHAs, advisory ids, symbols, URLs, snippets. Weights in use: 400, 600, 700. Scale ratio 1.25.

### Hierarchy
- **Display** (700, clamp(3.052rem, 2rem + 5vw, 5.5rem), 1.0, -0.04em, tabular): the two assay numerals only.
- **Headline** (700, 1.5625rem, 1.15, -0.02em): panel and section titles.
- **Title** (600, 1.25rem, 1.35): purpose line, reading sentence under the numerals.
- **Body** (400, 1rem, 1.5): all prose, capped near 68ch.
- **Label** (600, 0.8rem, +0.08em, uppercase): field labels (dt), table column heads, table captions. Never placed above a heading.
- **Data** (JetBrains Mono 400, 0.9em): file:line, ids, symbols, snippets; wrapped, never truncated.

### Named Rules
**The Plain Words Rule.** Primary labels are plain developer words ("Run assay", "Resolve dependencies", "Call path from your code"). Lab vocabulary lives in field and column labels ("Specimen", "Flag", "Method").
**The Tabular Rule.** Every figure uses tabular numerals so columns and counts align.

## Layout

A single screen with a 75rem content column, 16px gutters on phones, 24px from 48rem, 32px from 80rem. Spacing is a 4px scale (4, 8, 12, 16, 24, 32, 48, 64, 96). Idle: letterhead, then a two-thirds ruled "Order an assay" list beside a one-third live-repo form divided by a single hairline; the key to the three flags sits under the demos. After a run starts, the order panel collapses to a one-line bar, the letterhead's purpose line is dropped, the boxed specimen header with the six-stage processing track takes its place, and the assay numerals land inside the first 900px viewport, followed by the ruled results list with the top finding open.

Responsive: below 62rem the results list reflows to stacked label/value rows, the track to a vertical list and the evidence grid to one column. Nothing scrolls horizontally at 390px; wide tables scroll inside their own wrapper. Rhythm varies on purpose: 48px between report sections, 32px between page blocks, 8–16px within groups.

## Elevation & Depth

Flat. There are no shadows. Depth is a tonal step (Lab White to Report Sheet), a 1.5px ink outline for the specimen block and notices, and 1px hairlines between rows. The only transforms are a 1px press on buttons, the chevron rotation and the stamp.

### Named Rules
**The Flat Sheet Rule.** A surface is distinguished by a rule or a tint, never by a shadow or a blur.

## Shapes

Squared and instrument-like: 2px radius on controls, tags and boxes; 50% only on the numbered call-path nodes. Marks are drawn SVG on one 24px grid with a 1.5px stroke: filled triangle (likely reachable), hatched square with ink outline (uncertain), outline dash pill (no reachable path detected). One icon set, one stroke weight.

## Components

### Buttons
- **Shape:** 2px radius, 44px minimum height, 600 weight.
- **Primary** ("Run assay"): Report Blue fill, Report Sheet text, 8px 24px padding. Hover: Report Blue Deep. Active: 1px down.
- **Secondary / quiet** (live scan, export, order another): 1.5px outline in blue or control edge, transparent fill, blue tint on hover.
- **Disabled:** dashed control-edge outline, disabled fill, tertiary ink, `not-allowed` cursor.
- **Focus:** 3px Report Blue Deep outline, 2px offset, on every interactive element.

### Inputs / Fields
- **Style:** 1.5px control-edge stroke, Report Sheet fill, mono text, visible label above, hint beneath.
- **Focus:** border shifts to Report Blue Deep plus the global ring. **Error:** 3px ink border, `aria-invalid`, message in an ink-outlined notice linked by `aria-describedby`, input value kept. **Disabled:** dashed, filled.

### Specimen block (signature)
A boxed header: the repository set in mono at headline size, a three-column field grid of Source, Commit, Branch, Versions, Issued and Model, and a six-column processing track beneath a 1.5px rule. Each stage carries a stamp: pending (dashed), running (blue tint, arrow, one-shot fill bar), done (blue, check, rotated -2deg, stamped in with a 220ms scale-and-fade), failed (ink cross), skipped (dashed dash). Reduced motion: no stamp animation, no fill.

### Assay summary (signature)
Two display numerals set inline with their words: total advisories, a drawn arrow, then likely reachable. On landing the second numeral counts down from the total (1.1s, ease-out; final values only under reduced motion). A ruled tally table of the three verdicts, each with its mark, sits beside it.

### Result rows and findings
A header row of labels (Flag, Package, Advisory, Where, Confidence) over ruled rows. Each row is a button inside an h3; `likely reachable` rows tint red, `uncertain` rows tint amber and print their reason under the flag, `no reachable path detected` rows stay untinted. An opened row shows, in this order: vulnerable function, call path (numbered nodes on a hairline, dashed for heuristic edges, red sink), source locations with snippets, advisory sources (links open in a new tab with `noopener noreferrer`), then the uncertain reason box, confidence, limitations and the explanation. Uncertain findings carry `data-verdict="uncertain"`.

### Notices
Ink-outlined box with a drawn icon, a bold title, the API message verbatim, optional facts and a one-line recovery hint. Info notices (paused) use a dashed outline.

### Footnotes and sign-off
Method and limitations are numbered notes referenced from the summary; the report closes with a ruled sign-off line (issuer, date, model, scan id) and a one-sentence statement that it is automated analysis.

## Do's and Don'ts

### Do:
- **Do** keep red for `likely reachable` and amber for `uncertain`, always paired with the mark and the exact verdict words.
- **Do** put evidence (function, path, locations, sources) before confidence, limitations and explanation in DOM order.
- **Do** label demo and placeholder data wherever a figure appears.
- **Do** render all report text with `textContent`; link only http(s) with `rel="noopener noreferrer"`.
- **Do** keep every control at 44px or more with a visible 3px focus ring and a label.
- **Do** announce stage changes in the polite live region.

### Don't:
- **Don't** use stat tiles, coloured severity badges, or a wall of identical cards for findings.
- **Don't** add shadows, gradients, glass or glow; depth is a rule or a tint.
- **Don't** signal `uncertain` or any verdict by colour alone.
- **Don't** show a number that was not produced by a scan or labelled as demo data.
- **Don't** place kicker labels above headings; uppercase labels belong to fields, columns and captions.
- **Don't** hard-code colours, sizes or spaces outside the tokens at the top of `web/css/app.css`.
