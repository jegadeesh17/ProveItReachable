---
name: ProveItReachable
description: A calm developer-tool product. Evidence-first reachability triage, shown as a clean funnel, a visible agent pipeline and ranked findings.
colors:
  page: "#f6f7f9"
  surface: "#ffffff"
  surface-2: "#f0f2f5"
  hairline: "#e1e4ea"
  line-strong: "#c8cdd7"
  control-edge: "#7b8494"
  ink: "#10141b"
  ink-secondary: "#394152"
  ink-tertiary: "#576072"
  accent: "#2a55d1"
  accent-deep: "#1d3ea3"
  accent-tint: "#eaf0fd"
  accent-wash: "#f5f8ff"
  reach: "#c8301f"
  reach-deep: "#a02112"
  reach-tint: "#fdeceb"
  reach-wash: "#fff8f7"
  unsure: "#e0a826"
  unsure-edge: "#a77a0b"
  unsure-tint: "#fdf3d6"
  unsure-wash: "#fffaf0"
typography:
  display:
    fontFamily: "Geist, ui-sans-serif, system-ui, Segoe UI, Helvetica Neue, sans-serif"
    fontSize: "clamp(2rem, 1.3rem + 2.7vw, 3.05rem)"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.03em"
  numeral:
    fontFamily: "Geist, ui-sans-serif, system-ui, Segoe UI, Helvetica Neue, sans-serif"
    fontSize: "clamp(3rem, 1.6rem + 6vw, 5.25rem)"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "-0.04em"
    fontFeature: "tnum"
  headline:
    fontFamily: "Geist, ui-sans-serif, system-ui, Segoe UI, Helvetica Neue, sans-serif"
    fontSize: "1.953rem"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Geist, ui-sans-serif, system-ui, Segoe UI, Helvetica Neue, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.015em"
  body:
    fontFamily: "Geist, ui-sans-serif, system-ui, Segoe UI, Helvetica Neue, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  label:
    fontFamily: "Geist, ui-sans-serif, system-ui, Segoe UI, Helvetica Neue, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: 1.4
  data:
    fontFamily: "Geist Mono, ui-monospace, Cascadia Mono, SF Mono, Consolas, monospace"
    fontSize: "0.92em"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
  pill: "999px"
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
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "8px 24px"
    height: "52px"
  button-primary-hover:
    backgroundColor: "{colors.ink-secondary}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
    height: "44px"
  button-secondary-hover:
    backgroundColor: "{colors.surface-2}"
  input-field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
    height: "52px"
  demo-row:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "12px 16px"
  verdict-chip-reach:
    backgroundColor: "{colors.reach-tint}"
    textColor: "{colors.reach-deep}"
    rounded: "{rounded.pill}"
    padding: "4px 12px 4px 8px"
  verdict-chip-uncertain:
    backgroundColor: "{colors.unsure-tint}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "4px 12px 4px 8px"
  verdict-chip-none:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.pill}"
    padding: "4px 12px 4px 8px"
  summary-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "24px"
---

# Design System: ProveItReachable

## Overview

**Creative North Star: "The Clean Console"**

ProveItReachable is a developer tool, so it looks like one: a white-on-cool-grey product surface in the spirit of Linear, Vercel or Socket.dev, without borrowing any of their branding. Hairlines and tonal steps do the structuring; shadows are rare and soft; type is one confident sans plus a mono for everything a developer would copy. There is no document metaphor and no decoration. The product earns trust by showing its work: a one-line value proposition, one input, three one-click demos, then a result that leads with the funnel ("6 advisories to 2 likely reachable"), the pipeline that produced it, and the evidence for each finding.

Mode: **Persuade** on the front door (one message, one action: paste a repo or click a demo), **Operate** from the first scan on (scanability, density, consistency). The user is a Python developer drowning in scanner alerts; the cold evaluator has under three minutes and a laptop.

Evidence comes before explanation, everywhere. In every finding, the vulnerable function, call path, source locations and advisory links precede confidence, limitations and the model's explanation. The agent pipeline is visible while it runs and in the finished report: each stage names the tool behind it (OSV, Tavily, static analysis in a sandbox, Nemotron). Tavily research is credited on every advisory source it supplied.

**Key Characteristics:**
- Light, restrained: neutrals plus one accent; red and amber appear only on verdicts.
- Geist for interface and prose, Geist Mono for data (file:line, ids, symbols, URLs, tool names).
- Verdicts are always shape plus words; colour only reinforces.
- Generous whitespace, 8 to 12px radii, 1px hairlines, one soft shadow reserved for the summary panel and the example card.
- Left-aligned, asymmetric layout; no centred hero, no grid of identical cards.

## Colors

A cool-tinted neutral ramp, one accent, and two reserved verdict colours.

### Primary
- **Ink** (#10141b): text, the primary button, done-stage nodes. The primary action is ink, not accent.
- **Accent** (#2a55d1): links, keyboard focus, the running stage, the Tavily credit. Used on well under a tenth of any screen. **Accent Deep** (#1d3ea3) for text on accent tints and hover.

### Secondary (reserved)
- **Reach** (#c8301f) and **Reach Deep** (#a02112): only `likely reachable`: the filled triangle, the chip, the sink node of a call path and the numeral of the funnel.
- **Unsure** (#e0a826) and **Unsure Edge** (#a77a0b): only `uncertain`: the hatched square, the chip, the reason box. Never used as text.

### Neutral
- **Page** (#f6f7f9), **Surface** (#ffffff), **Surface 2** (#f0f2f5): ground, raised surfaces, quiet fills and code wells.
- **Hairline** (#e1e4ea), **Line Strong** (#c8cdd7), **Control Edge** (#7b8494, 3.5:1 or better on page and surface): rules and control boundaries.
- **Ink Secondary** (#394152, 10:1) and **Ink Tertiary** (#576072, 5.6:1 or better on every ground used): secondary text and meta.
- **Reach Wash** (#fff8f7) and **Unsure Wash** (#fffaf0) tint finding rows; **Accent Wash** (#f5f8ff) tints the running stage.

### Named Rules
**The Reserved Colour Rule.** Red means `likely reachable` and nothing else; amber means `uncertain` and nothing else. Errors, warnings and success states use ink outlines and drawn icons, never these colours. Done stages are ink, not green: green would imply "safe".
**The Never Colour Alone Rule.** Every verdict is a mark (filled triangle, hatched square, outline pill) plus its exact words. Every stage state is a drawn node (ring, arc, check, cross, dash) plus a visually hidden state word.

## Typography

**Interface and prose:** Geist (with ui-sans-serif, system-ui, Segoe UI)
**Data:** Geist Mono (with ui-monospace, Cascadia Mono, Consolas)

**Character:** a precise, slightly geometric sans that stays neutral at body size and sharpens at display size; mono is used only for things a developer would copy. Weights in use: 400, 500, 600. Scale ratio 1.25 from 16px.

### Hierarchy
- **Display** (600, clamp 2rem to 3.05rem, 1.1, -0.03em): the front-door headline only.
- **Numeral** (600, clamp 3rem to 5.25rem, 1.0, -0.04em, tabular): the funnel figures only.
- **Headline** (600, 1.953rem, 1.15, -0.025em): the repository name (`owner/` in tertiary ink, the name in ink).
- **Title** (600, 1.25rem, 1.25): section headings (Findings, Method and limitations, How a scan works).
- **Body** (400, 1rem, 1.5): prose, capped near 68ch.
- **Label** (500, 0.875rem): field labels, column heads, block titles. Sentence case; no uppercase kickers.
- **Data** (Geist Mono 400 to 500, 0.92em): file:line, ids, symbols, snippets, tool chips; wrapped, never truncated.

### Named Rules
**The Plain Words Rule.** Primary labels are plain developer words: "Scan repo", "Run demo", "New scan", "Call path from your code", "Findings".
**The Tabular Rule.** Every figure uses tabular numerals so counts and durations align.

## Layout

A single screen with a 75rem content column, 16px gutters on phones, 24px from 48rem, 32px from 80rem. Spacing is the 4px scale (4, 8, 12, 16, 24, 32, 48, 64, 96).

**Front door (idle):** a product header (wordmark with a drawn call-path glyph on the left, "Powered by Nebius Token Factory · Nemotron · Tavily" quietly on the right). Below, two columns from 62rem: left, the headline, one sentence of value, and the URL input joined to the primary "Scan repo" button; right, "Or run a recorded demo" with three one-click rows, each showing the recorded verdict counts. Below the fold: "How a scan works" (six stages, each with its tool) beside an example finding and the three-verdict key.

**Scanning:** the pitch disappears. The repository name and facts sit on top with "New scan" at the right. One panel holds the live pipeline: a step label, an elapsed clock, a meter, and six rows (node, stage, tool chip, the stage's message, duration). The running row is tinted with accent wash and spins its node.

**Report:** repository header with New scan and the two export buttons; the summary panel (funnel numerals, a strip with one mark per finding, the reading sentence, the verdict tally, and a "How this report was made" pipeline strip); then Findings as one bordered list with a column header on desktop; then unresolved dependencies, method and limitations, and a quiet report footer.

Responsive: below 62rem the findings reflow to stacked label/value rows and the pipeline rows to node, name, tool, message; the evidence grid becomes one column. Nothing scrolls horizontally at 375px; wide tables scroll inside their own wrapper or stack. Rhythm varies on purpose: 48 to 64px between major blocks, 24px between report sections, 8 to 16px inside groups.

## Elevation & Depth

Mostly flat: structure is a 1px hairline or a tonal step (Page to Surface to Surface 2). Two soft, offset shadows exist: a hairline-small one on inputs and list containers, and a slightly deeper one (4px offset, 8px blur) on the summary panel, the example card and hovered demo rows. No glow, no glass.

### Named Rules
**The Quiet Depth Rule.** Shadows carry an offset and a soft blur and are used on at most one raised object per region; a surface is otherwise separated by a rule or a tint.

## Shapes

Three radii by role: 6px for chips, code wells and tags; 8px for controls; 12px for panels and lists; pill only for verdict chips. Pills, tags and tool chips keep at least 4px vertical and 8px horizontal padding; bordered lists inset their rows by 16px. Marks are drawn SVG on a 24px grid: filled triangle (likely reachable), hatched square with an ink outline (uncertain), outline pill (no reachable path detected). One icon set, one stroke weight (1.8).

## Components

### Buttons
- **Primary** ("Scan repo"): ink fill, white text, 52px tall, 8px radius. Hover: ink-secondary. One per view.
- **Secondary** (New scan, downloads): surface fill, 1px line-strong border, 44px minimum height. Hover: surface-2 and control-edge border.
- **Disabled:** dashed border, disabled fill, tertiary ink, `not-allowed`.
- **Focus:** 2px accent outline, 2px offset (inside the box on list rows), on every interactive element.

### Input
- 52px tall, mono text, visible label above (never placeholder-only), hint beneath, 1px control-edge border with a soft shadow.
- Focus: border turns accent plus the global ring. Error: 2px ink border, `aria-invalid`, message in an ink-outlined notice linked by `aria-describedby`, the typed value kept. Disabled: dashed and filled.

### Demo rows (signature)
Each demo is one full-width button: repo name in mono with a "placeholder" tag, a one-line description, the recorded verdict counts as small marks, and "Run demo" with an arrow on the right. Hover lifts with the soft shadow and nudges the arrow 3px.

### Verdict chip
Pill with the verdict's mark and its exact words. `likely reachable`: reach tint, reach border, reach-deep text. `uncertain`: unsure tint with an unsure-edge border and ink text. `no reachable path detected`: white with a control-edge border.

### Pipeline (signature)
The same six stages appear three times: on the front door as a list of what each stage does, live while scanning, and in the finished summary as a strip with durations. Each stage always shows its tool: Lockfile and uv, OSV, Tavily, static analysis in a sandbox, Nemotron, verdict rules. States: pending (empty ring), running (accent arc, tinted row), done (ink check), failed (outlined cross), skipped (dashed dash).

### Summary panel
White panel with the soft shadow. The funnel line sets two numerals in the numeral style with their words and a drawn arrow; the second numeral is reach-deep and counts down from the total on landing (final value only under reduced motion). Under it, one mark per finding in rank order, staggered in. The tally lists the three verdicts with their marks. Clean and incomplete outcomes show "0" with the dependency counts instead.

### Findings list
One bordered container. Each finding is a button inside an h3: verdict chip (uncertain rows print the reason), package and version, advisory id, first location, confidence, chevron. `likely reachable` rows carry a faint red wash, `uncertain` rows a faint amber wash, `no reachable path detected` rows stay white. An opened finding shows, in this order: vulnerable function, call path (numbered nodes on a rail, dashed for heuristic edges, reach-tinted sink), where your code touches it with snippets, advisory sources, then a tinted assessment block (uncertain reason, confidence, limitations, explanation). Uncertain findings carry `data-verdict="uncertain"`.

### Advisory sources
Each link is the exact report URL with `rel="noopener noreferrer"`, followed by a source badge and the URL in mono. Links from Tavily research carry an accent badge with a search icon reading "Tavily research"; OSV links carry a neutral "OSV" badge. A line above the list states how many sources came from Tavily research.

### Notices
Surface box with a 1px ink border, a drawn icon, the API message verbatim as the bold lead (no separate title that would repeat it; a title appears only on client-side notices), optional facts and a one-line recovery hint. Info notices (paused) use a dashed border.

## Do's and Don'ts

### Do:
- **Do** keep red for `likely reachable` and amber for `uncertain`, always paired with the mark and the exact verdict words.
- **Do** put evidence (function, path, locations, sources) before confidence, limitations and explanation in DOM order.
- **Do** show the tool behind every pipeline stage and credit Tavily on the sources it found.
- **Do** label demo and placeholder data wherever a figure appears; take any metric from a fixture field and name its origin.
- **Do** render all report text with `textContent`; link only http(s) with `rel="noopener noreferrer"`.
- **Do** keep every control at 44px or more with a visible focus ring and a label.
- **Do** announce stage changes in the polite live region.

### Don't:
- **Don't** invent metrics, benchmarks, logos or user counts. The powered-by line is plain text, with no brand marks.
- **Don't** use gradients, glass, glow or hard offset shadows.
- **Don't** signal any verdict or stage state by colour alone.
- **Don't** use green for done: it reads as "safe".
- **Don't** build a centred hero, a grid of identical feature cards, or kicker labels above headings.
- **Don't** repeat a message as its own title, or start a wrapped line with a middle dot (glue it to the word before it).
- **Don't** hard-code colours, sizes, spaces or durations outside `web/css/tokens.css`.
