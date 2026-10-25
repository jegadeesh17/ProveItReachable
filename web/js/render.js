/**
 * DOM building for the single screen.
 *
 * Every piece of report text (advisory summaries, model explanations, snippets, URLs) is untrusted and
 * reaches the page only through textContent or createElement. No HTML is ever parsed from a string here.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

/* ------------------------------------------------------------------ helpers */

/**
 * Create an element. Props: class, text, dataset, on, any other key becomes an attribute.
 * Children may be Nodes, strings (appended as text) or arrays of those.
 */
export function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "on") {
      for (const [name, handler] of Object.entries(value)) node.addEventListener(name, handler);
    } else node.setAttribute(key, value === true ? "" : String(value));
  }
  appendKids(node, kids);
  return node;
}

function appendKids(node, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

/** A reference into the sprite in index.html. Decorative unless a label is passed. */
export function icon(id, className = "icon") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#${id}`);
  svg.append(use);
  return svg;
}

export const plural = (n, one, many) => (n === 1 ? one : many);

const dateFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const timeFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });

export function formatDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "unknown date" : dateFmt.format(d);
}
export function formatDateTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "unknown date" : `${dateFmt.format(d)}, ${timeFmt.format(d)} UTC`;
}

function secondsBetween(a, b) {
  if (!a || !b) return null;
  const s = (new Date(b) - new Date(a)) / 1000;
  return Number.isFinite(s) && s >= 0 ? s : null;
}
function formatSeconds(s) {
  if (s === null) return "";
  return s < 1 ? "under 1 s" : `${Math.round(s)} s`;
}

/**
 * A link only if the address is http(s). The href is the exact report string.
 * Anything else is shown as inert text, never as a link.
 */
export function safeLink(url, label) {
  let ok = false;
  try {
    const parsed = new URL(url);
    ok = parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    ok = false;
  }
  if (!ok) {
    return el("span", {}, label || url, " ", el("span", { class: "hint", text: "(not linked: not an http or https address)" }));
  }
  const a = document.createElement("a");
  a.setAttribute("href", url);
  a.setAttribute("target", "_blank");
  a.setAttribute("rel", "noopener noreferrer");
  a.textContent = label || url;
  a.append(" ", icon("icon-external", "icon icon--sm"), el("span", { class: "sr-only", text: "(opens in a new tab)" }));
  return a;
}

/* ------------------------------------------------------------------ verdicts */

export const VERDICTS = {
  "likely reachable": { key: "reach", mark: "mark-reach" },
  uncertain: { key: "uncertain", mark: "mark-uncertain" },
  "no reachable path detected": { key: "none", mark: "mark-none" },
};

/** Shape plus words: the mark never carries the verdict alone. */
export function flag(verdict) {
  const meta = VERDICTS[verdict];
  return el("span", { class: `flag flag--${meta ? meta.key : "none"}` }, icon(meta ? meta.mark : "mark-none"), el("span", { text: verdict }));
}

const REASON_LABEL = {
  vulnerable_function_not_identified: "vulnerable function not identified",
  tavily_failed: "Tavily research failed",
  nebius_failed: "Nebius request failed",
  model_output_invalid: "model output invalid",
  unsupported_reachability_claim: "reachability claim not supported by the call path",
  contradicted_by_index: "contradicted by the code index",
  osv_failed: "OSV did not answer",
  sandbox_timeout: "sandbox timeout",
  sandbox_failed: "sandbox failed",
  spend_cap_reached: "spend cap reached",
  scan_budget_reached: "scan budget reached",
  scan_timeout: "scan timed out",
  finding_limit_reached: "finding limit reached",
};
const EDGE_LABEL = {
  entry: "entry point",
  call: "call",
  heuristic_call: "heuristic call",
  vulnerable_call: "vulnerable call",
};

/* ------------------------------------------------------------------ banners */

const ERROR_TITLE = {
  invalid_url: "That is not a repository address",
  repo_not_found: "Repository not found",
  repo_too_large: "Repository is too large",
  not_python_project: "Not a Python project",
  rate_limited: "Live scans are rate limited",
  live_scans_paused: "Live scans are paused",
  server_busy: "The scanner is busy",
  github_unavailable: "GitHub did not respond",
  internal_error: "Something went wrong",
};
const ERROR_HINT = {
  invalid_url: "Check the address and try again, or run a demo, which needs no address.",
  repo_not_found: "Check the spelling. Private repositories cannot be scanned. A demo works without an address.",
  repo_too_large: "Try a smaller repository, or run a demo.",
  not_python_project: "A live scan needs at least one .py file and a dependency manifest, such as pyproject.toml or requirements.txt, at the repository root.",
  rate_limited: "Demo replays are not rate limited, so you can keep exploring.",
  live_scans_paused: "Pick a demo to see a full report.",
  server_busy: "Demo replays are still available.",
  github_unavailable: "Nothing was scanned. Try again later, or run a demo.",
  internal_error: "Nothing was lost. Try again, or run a demo.",
};

/**
 * @param {{ code?: string, message: string, details?: object }} error  message is shown verbatim (AC-43)
 * @param {{ kind?: "error"|"info", title?: string }} [options]
 */
export function banner(error, options = {}) {
  const kind = options.kind || "error";
  const code = error.code || "";
  const facts = [];
  if (code === "repo_too_large" && error.details) {
    const d = error.details;
    facts.push(["Limit", `${d.max_mb} MB or ${d.max_files} files`]);
    facts.push(["This repository", `${d.mb} MB in ${d.files} files`]);
  }
  const hint = ERROR_HINT[code];
  return el(
    "div",
    { class: `notice notice--${kind}`, role: kind === "error" ? "alert" : "status" },
    icon(kind === "error" ? "icon-alert" : "icon-info"),
    el(
      "div",
      { class: "notice__body" },
      el("p", { class: "notice__title", text: options.title || ERROR_TITLE[code] || "Could not start the assay" }),
      el("p", { class: "notice__msg", text: error.message }),
      facts.length > 0 &&
        el("dl", { class: "notice__facts" }, facts.map(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })])),
      hint && el("p", { class: "notice__hint", text: hint }),
    ),
  );
}

/* --------------------------------------------------------------- demo list */

export function renderDemoSkeleton(list) {
  list.setAttribute("aria-busy", "true");
  clear(list);
  for (let i = 0; i < 3; i += 1) {
    list.append(
      el(
        "li",
        { class: "demo-row", "aria-hidden": "true" },
        el("div", {}, el("span", { class: "skel skel--title" }), el("span", { class: "skel skel--line" })),
        el("span", { class: "skel skel--btn" }),
      ),
    );
  }
}

export function renderDemoError(list, message, onRetry) {
  list.setAttribute("aria-busy", "false");
  clear(list);
  list.append(
    el(
      "li",
      { class: "demo-row" },
      banner({ code: "", message }, { title: "The demo list did not load" }),
      el("button", { class: "btn", type: "button", text: "Try again", on: { click: onRetry } }),
    ),
  );
}

export function renderDemos(list, demos, onRun) {
  list.setAttribute("aria-busy", "false");
  clear(list);
  for (const demo of demos) {
    list.append(
      el(
        "li",
        { class: "demo-row" },
        el(
          "div",
          {},
          el("p", { class: "demo-name" }, el("span", { text: demo.title }), el("span", { class: "tag", text: "Placeholder repo" })),
          el("p", { class: "demo-desc", text: demo.description }),
        ),
        el("button", {
          class: "btn btn--primary",
          type: "button",
          text: "Run assay",
          "aria-label": `Run assay on ${demo.title}`,
          dataset: { demo: demo.slug },
          on: { click: () => onRun(demo) },
        }),
      ),
    );
  }
}

export function setDemosDisabled(list, disabled) {
  for (const button of list.querySelectorAll("button[data-demo]")) button.disabled = disabled;
}

/* ------------------------------------------------------------- process track */

export const STAGES = [
  ["resolve", "Resolve dependencies"],
  ["osv", "Query OSV"],
  ["research", "Research advisories"],
  ["analysis", "Analyze code"],
  ["reasoning", "Reason over evidence"],
  ["report", "Write verdicts"],
];
const STAMP_ICON = { running: "stamp-running", done: "stamp-done", failed: "stamp-failed", skipped: "stamp-skipped" };

function fillStamp(stamp, state) {
  clear(stamp);
  if (STAMP_ICON[state]) stamp.append(icon(STAMP_ICON[state]));
  stamp.append(state);
}

export function buildTrack(track) {
  clear(track);
  for (const [key, label] of STAGES) {
    const stamp = el("span", { class: "stamp" });
    fillStamp(stamp, "pending");
    track.append(
      el(
        "li",
        { class: "stage", dataset: { step: key, state: "pending" } },
        el("div", { class: "stage__stamp" }, stamp),
        el("p", { class: "stage__name", text: label }),
        el("p", { class: "stage__msg" }),
        el("p", { class: "stage__time" }),
        el("span", { class: "stage__bar", "aria-hidden": "true" }),
      ),
    );
  }
}

export function stageLabel(step) {
  const found = STAGES.find(([key]) => key === step);
  return found ? found[1] : step;
}

/** Move one stage to a state. `stamp` replays the stamp-in motion (CSS drops it for reduced motion). */
export function setStage(track, step, state, message, seconds, stamp = false) {
  const li = track.querySelector(`[data-step="${step}"]`);
  if (!li) return;
  const changed = li.dataset.state !== state;
  li.dataset.state = state;
  fillStamp(li.querySelector(".stamp"), state);
  li.querySelector(".stage__msg").textContent = message || "";
  li.querySelector(".stage__time").textContent = seconds === null || seconds === undefined ? "" : formatSeconds(seconds);
  if (stamp && changed && ["done", "failed", "skipped"].includes(state)) {
    li.dataset.stamped = "false";
    void li.offsetWidth;
    li.dataset.stamped = "true";
  }
}

export function syncTrackFromReport(track, report) {
  for (const step of report.steps) {
    setStage(track, step.name, step.state, step.message, secondsBetween(step.started_at, step.finished_at), false);
  }
}

/* ------------------------------------------------------------------ specimen */

function fieldRow(label, value, extra = {}) {
  return el(
    "div",
    { class: `field-row${extra.lead ? " field-row--lead" : ""}` },
    el("dt", { class: "label", text: label }),
    el("dd", {}, value),
  );
}

/**
 * @param {HTMLElement} dl
 * @param {{ name: string, source: string, commit?: string|null, branch?: string, resolvedBy?: string, issued?: string, model?: string, scanId?: string }} info
 */
export function renderSpecimen(dl, info) {
  clear(dl);
  dl.append(fieldRow("Specimen", el("h2", { id: "specimen-h", tabindex: "-1", text: info.name }), { lead: true }));
  dl.append(fieldRow("Source", info.source));
  dl.append(
    fieldRow("Commit", info.commit ? el("span", { class: "mono", text: info.commit }) : el("span", { class: "pending-text", text: "Resolved when the scan starts" })),
  );
  if (info.branch) dl.append(fieldRow("Branch", el("span", { class: "mono", text: info.branch })));
  if (info.resolvedBy) dl.append(fieldRow("Versions", info.resolvedBy));
  if (info.issued) dl.append(fieldRow("Issued", info.issued));
  if (info.model) dl.append(fieldRow("Model", el("span", { class: "mono", text: info.model })));
}

/* ------------------------------------------------------------ assay summary */

function countDown(node, from, to, ms) {
  const start = performance.now();
  node.textContent = String(from);
  const tick = (now) => {
    const t = Math.min(1, (now - start) / ms);
    const eased = 1 - Math.pow(1 - t, 3);
    node.textContent = String(Math.round(from + (to - from) * eased));
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function noteRef(text, href) {
  return el("sup", {}, el("a", { href, text, "aria-label": `Note ${text}` }));
}

/**
 * The assay summary: two large numerals (advisories down to likely reachable), a reading, a tally.
 * Returns { node, exportSlot, animate(reduceMotion) }.
 */
export function renderSummary(report, { isMock }) {
  const s = report.summary;
  const by = s.by_verdict;
  const lr = by["likely reachable"];
  const un = by.uncertain;
  const np = by["no reachable path detected"];
  const advisories = report.findings.filter((f) => f.kind === "advisory").length;
  const total = s.findings_total;
  const exportSlot = el("div", { class: "head-actions" });

  let figure;
  let animate = () => {};
  let reading;
  let tally;

  if (report.outcome === "findings") {
    const fromEl = el("span", { class: "num", text: String(total) });
    const toEl = el("span", { class: "num num--reach", text: String(lr) });
    const noun = total === advisories ? plural(total, "advisory", "advisories") : plural(total, "finding", "findings");
    figure = [
      el(
        "p",
        { class: "assay__line", "aria-hidden": "true" },
        fromEl,
        el("span", { text: noun }),
        icon("icon-arrow", "icon icon--arrow"),
        toEl,
        el("span", { text: "likely reachable" }),
      ),
      el("p", { class: "sr-only", text: `${total} ${noun}, ${lr} likely reachable.` }),
    ];
    animate = (reduce) => {
      if (reduce || total === lr) return;
      countDown(toEl, total, lr, 1100);
    };
    const parts = [];
    parts.push(lr > 0 ? `${lr} ${plural(lr, "is", "are")} likely reachable and ${plural(lr, "needs", "need")} attention first.` : "None is likely reachable.");
    if (un > 0) {
      parts.push(`${un} ${plural(un, "is", "are")} uncertain: read the reason printed with ${plural(un, "it", "each")}.`);
    }
    reading = el("p", { class: "assay__reading" }, parts.join(" "));
    if (np > 0) {
      reading.append(` ${np} had no reachable path detected within the scope stated in note `, noteRef("2", "#fn-scope"), ".");
    }
    if (lr === 0 && un > 0) reading.append(" An uncertain result is not a pass.");
    if (s.dependencies_not_checked > 0) {
      reading.append(
        ` ${s.dependencies_not_checked} ${plural(s.dependencies_not_checked, "dependency", "dependencies")} could not be checked and ${plural(s.dependencies_not_checked, "is", "are")} not counted as clean.`,
      );
    }
    tally = el(
      "table",
      { class: "tally" },
      el("caption", { class: "label", text: "Verdicts" }),
      el(
        "tbody",
        {},
        [
          ["likely reachable", lr],
          ["uncertain", un],
          ["no reachable path detected", np],
        ].map(([verdict, n]) => el("tr", {}, el("th", { scope: "row" }, flag(verdict)), el("td", { class: "n", text: String(n) }))),
      ),
    );
  } else {
    const isClean = report.outcome === "clean";
    figure = [
      el(
        "p",
        { class: "assay__line", "aria-hidden": "true" },
        el("span", { class: "num", text: "0" }),
        el("span", {
          text: isClean
            ? `known advisories in ${s.dependencies_total} ${plural(s.dependencies_total, "dependency", "dependencies")}`
            : `advisories in ${s.dependencies_checked} of ${s.dependencies_total} dependencies`,
        }),
      ),
      el("p", { class: "sr-only", text: isClean ? "Clean: no known advisories." : "Incomplete: no advisories among the dependencies that could be checked." }),
    ];
    reading = el("p", {
      class: "assay__reading",
      text: isClean
        ? `No known-vulnerable dependencies were found. All ${s.dependencies_checked} dependencies were checked against OSV at their exact versions.`
        : `Not a clean result. ${s.dependencies_checked} of ${s.dependencies_total} dependencies were checked and have no known advisories. ${s.dependencies_unresolved} could not be resolved to an exact version, so ${plural(s.dependencies_unresolved, "it was", "they were")} not examined: see the list below.`,
    });
    tally = el(
      "table",
      { class: "tally" },
      el("caption", { class: "label", text: "Dependencies" }),
      el(
        "tbody",
        {},
        [
          ["Checked, no advisories", s.dependencies_checked - s.dependencies_not_checked],
          ["Unresolved", s.dependencies_unresolved],
          ["Not checked", s.dependencies_not_checked],
        ].map(([label, n]) => el("tr", {}, el("th", { scope: "row", text: label }), el("td", { class: "n", text: String(n) }))),
      ),
    );
  }

  const heading = report.outcome === "clean" ? "Assay summary: clean" : report.outcome === "incomplete" ? "Assay summary: incomplete" : "Assay summary";
  const node = el(
    "section",
    { class: `assay assay--${report.outcome}`, "aria-labelledby": "assay-h", dataset: { outcome: report.outcome } },
    el(
      "div",
      { class: "section-head" },
      el("div", {}, el("h2", { id: "assay-h", class: "section-title", text: heading })),
      el("div", { class: "head-actions" }, isMock && el("span", { class: "tag", text: "Demo data" }), exportSlot),
    ),
    el("div", { class: "assay__body" }, el("div", {}, figure, reading), tally),
  );
  return { node, exportSlot, animate };
}

/* ------------------------------------------------------------------ results */

function firstLocation(finding) {
  const locs = finding.source_locations;
  return locs.find((l) => l.kind === "call") || locs[0] || null;
}

function cell(label, ...kids) {
  return el("span", { class: label ? "cell cell--fact" : "cell" }, label && el("span", { class: "cell-label", text: label }), el("span", {}, kids));
}

function dtRow(term, value) {
  return el("div", {}, el("dt", { text: term }), el("dd", {}, value));
}

function renderEvidence(f) {
  const evidence = el("div", { class: "evidence" });

  if (f.vulnerable_symbol) {
    const vs = f.vulnerable_symbol;
    evidence.append(
      el(
        "div",
        { class: "ev-fn" },
        el("h4", { class: "block-title", text: "Vulnerable function" }),
        vs.identified
          ? el("div", { class: "syms" }, vs.symbols.map((symbol) => el("code", { class: "sym", text: symbol })))
          : el("p", { class: "none-found", text: vs.not_identified_reason || "vulnerable function not identified" }),
      ),
    );
  }

  if (f.call_path && f.call_path.length) {
    evidence.append(
      el(
        "div",
        { class: "ev-path" },
        el("h4", { class: "block-title", text: "Call path from your code" }),
        el(
          "ol",
          { class: "callpath" },
          f.call_path.map((step, i) => {
            const sink = step.edge === "vulnerable_call";
            return el(
              "li",
              { dataset: { edge: step.edge } },
              el("span", { class: `n${sink ? " n--sink" : ""}`, text: String(i + 1) }),
              el(
                "div",
                {},
                el("p", { class: "where", text: `${step.file}:${step.line}` }),
                el(
                  "p",
                  { class: "what" },
                  el("code", { text: step.symbol }),
                  el("span", { class: `edge${step.edge === "heuristic_call" ? " edge--heuristic" : ""}` }, sink && icon("mark-reach", "icon icon--sm"), sink ? " " : "", EDGE_LABEL[step.edge] || step.edge),
                ),
              ),
            );
          }),
        ),
      ),
    );
  }

  if (f.kind === "advisory") {
    evidence.append(
      el(
        "div",
        { class: "ev-locs" },
        el("h4", { class: "block-title", text: "Where your code touches it" }),
        f.source_locations.length
          ? el(
              "ul",
              { class: "locs" },
              f.source_locations.map((loc) =>
                el(
                  "li",
                  {},
                  el("span", {}, el("span", { class: "where", text: `${loc.file}:${loc.line}` }), el("span", { class: "kind", text: loc.kind })),
                  loc.snippet && el("code", { class: "snippet", text: loc.snippet }),
                ),
              ),
            )
          : el("p", { class: "none-found", text: f.verdict === "uncertain" ? "No locations recorded for this finding." : "No import or call site found in repository code." }),
      ),
    );

    const adv = f.advisory;
    evidence.append(
      el(
        "div",
        { class: "ev-links" },
        el("h4", { class: "block-title", text: "Advisory sources" }),
        el(
          "ul",
          { class: "links" },
          f.advisory_links.map((link) =>
            el(
              "li",
              {},
              safeLink(link.url, link.title || link.url),
              el("span", { class: "src" }, `${link.source === "osv" ? "OSV" : "Tavily"}`, link.title ? [" · ", el("code", { text: link.url })] : ""),
            ),
          ),
        ),
        adv &&
          el(
            "dl",
            { class: "kv" },
            dtRow("Advisory", el("code", { text: adv.id })),
            adv.aliases.length > 0 && dtRow("Aliases", el("code", { text: adv.aliases.join(", ") })),
            dtRow("Severity", adv.severity),
            adv.fixed_versions.length > 0 && dtRow("Fixed in", el("code", { text: adv.fixed_versions.join(", ") })),
            adv.summary && dtRow("Summary", adv.summary),
          ),
      ),
    );
  } else {
    evidence.append(
      el("div", { class: "ev-locs" }, el("h4", { class: "block-title", text: "Evidence" }), el("p", { class: "none-found", text: "No advisory was retrieved for this package, so there is no evidence to show." })),
    );
  }
  return evidence;
}

function renderAssessment(f) {
  const box = el("div", { class: "assessment" });
  if (f.verdict === "uncertain" && f.reason) {
    box.append(
      el(
        "div",
        { class: "reason-box" },
        el("h4", { class: "block-title", text: "Why this is uncertain" }),
        el("p", { text: f.reason }),
        f.reason_code && el("p", { class: "hint" }, "Reason code: ", el("code", { text: f.reason_code })),
      ),
    );
  }
  box.append(
    el("div", {}, el("h4", { class: "block-title", text: "Confidence" }), el("p", { class: "conf", text: f.confidence })),
    el("div", {}, el("h4", { class: "block-title", text: "Limitations" }), el("ul", { class: "bullets" }, f.limitations.map((x) => el("li", { text: x })))),
    el("div", {}, el("h4", { class: "block-title", text: "Explanation" }), el("p", { class: "explanation", text: f.explanation })),
  );
  return box;
}

function renderFinding(f, index) {
  const meta = VERDICTS[f.verdict] || VERDICTS["no reachable path detected"];
  const id = `finding-${f.finding_id}`;
  const bodyId = `${id}-body`;
  const loc = firstLocation(f);
  const body = el("div", { class: "finding__body", id: bodyId, hidden: true }, renderEvidence(f), renderAssessment(f));

  const toggle = el(
    "button",
    { class: "finding__toggle cols", type: "button", "aria-expanded": "false", "aria-controls": bodyId },
    el(
      "span",
      { class: "cell" },
      flag(f.verdict),
      f.verdict === "uncertain" && f.reason_code && el("span", { class: "cell__sub", text: `Reason: ${REASON_LABEL[f.reason_code] || f.reason_code}` }),
    ),
    cell("Package", el("span", { class: "pkg", text: f.package }), " ", el("span", { class: "ver", text: f.version })),
    cell(
      "Advisory",
      f.advisory ? el("code", { text: f.advisory.id }) : el("span", { class: "cell__none", text: "not checked" }),
      f.advisory && f.advisory.aliases.length > 0 && el("span", { class: "cell__sub" }, el("code", { text: f.advisory.aliases[0] })),
    ),
    cell(
      "Where",
      loc ? el("code", { text: `${loc.file}:${loc.line}` }) : el("span", { class: "cell__none", text: f.kind !== "advisory" ? "not examined" : f.verdict === "uncertain" ? "none recorded" : "no call site found" }),
    ),
    cell("Confidence", f.confidence),
    icon("icon-chevron", "icon finding__chev"),
  );
  toggle.addEventListener("click", () => setOpen(toggle, body, toggle.getAttribute("aria-expanded") !== "true"));

  return el(
    "li",
    {},
    el(
      "article",
      { class: `finding finding--${meta.key}`, id, dataset: { verdict: f.verdict }, "aria-label": `${f.verdict}: ${f.package} ${f.version}` },
      el("h3", { class: "finding__h" }, toggle),
      body,
    ),
  );
}

function setOpen(toggle, body, open) {
  toggle.setAttribute("aria-expanded", String(open));
  body.hidden = !open;
}

export function renderResults(report) {
  const head = el(
    "div",
    { class: "results__head cols" },
    ...["Flag", "Package", "Advisory", "Where", "Confidence"].map((t) => el("span", { class: "label", text: t })),
    el("span"),
  );
  const list = el("ol", { class: "results" }, report.findings.map((f, i) => renderFinding(f, i)));
  const node = el(
    "section",
    { class: "results-section", "aria-labelledby": "results-h" },
    el(
      "div",
      { class: "section-head" },
      el(
        "div",
        {},
        el("h2", { id: "results-h", class: "section-title", text: "Results" }),
        el("p", { class: "section-sub", text: "Ranked: likely reachable first, then uncertain, then no reachable path detected; within each, higher confidence first. Open a row for the evidence." }),
      ),
    ),
    head,
    list,
  );
  // The top-ranked finding opens first, with its evidence beneath the row.
  const first = list.querySelector(".finding__toggle");
  if (first) setOpen(first, list.querySelector(".finding__body"), true);
  return node;
}

/* ------------------------------------------------- unresolved, method, signoff */

export function renderNotExamined(report) {
  const rows = report.dependencies.filter((d) => !d.resolved || d.osv_status === "not_checked");
  if (rows.length === 0) return null;
  return el(
    "section",
    { class: "not-examined", id: "not-examined", "aria-labelledby": "notexam-h" },
    el(
      "div",
      { class: "section-head" },
      el(
        "div",
        {},
        el("h2", { id: "notexam-h", class: "section-title", text: "Unresolved and unchecked dependencies" }),
        el("p", { class: "section-sub", text: "These were not checked against OSV, so they are not counted as clean. An unresolved dependency has no exact version, and a version is never guessed." }),
      ),
    ),
    el(
      "div",
      { class: "dtable-wrap" },
      el(
        "table",
        { class: "dtable dtable--stack" },
        el("thead", {}, el("tr", {}, el("th", { scope: "col", text: "Package" }), el("th", { scope: "col", text: "Status" }), el("th", { scope: "col", text: "Reason" }))),
        el(
          "tbody",
          {},
          rows.map((d) =>
            el(
              "tr",
              {},
              el("td", { class: "pkgcell", "data-label": "Package" }, d.name, d.version ? [" ", el("span", { class: "ver", text: d.version })] : ""),
              el("td", { "data-label": "Status", text: d.resolved ? "not checked" : "unresolved" }),
              el("td", { "data-label": "Reason", text: (d.resolved ? d.osv_error : d.unresolved_reason) || "No reason recorded." }),
            ),
          ),
        ),
      ),
    ),
  );
}

export function renderMethod(report) {
  const r = report.resolution;
  let resolution;
  if (r.method === "lockfile") resolution = `Exact versions come from ${r.lockfile}. No version was guessed.`;
  else if (r.method === "compiled") resolution = `Versions were compiled from ${r.compiled_from || "the manifest"} with uv pip compile${r.python_version ? ` for Python ${r.python_version}` : ""}, not locked.`;
  else resolution = "Dependency versions could not be resolved.";

  const depsTable = el(
    "div",
    { class: "dtable-wrap" },
    el(
      "table",
      { class: "dtable dtable--stack" },
      el("thead", {}, el("tr", {}, ...["Package", "Version", "Source", "Declared", "OSV"].map((t) => el("th", { scope: "col", text: t })))),
      el(
        "tbody",
        {},
        report.dependencies.map((d) =>
          el(
            "tr",
            {},
            el("td", { class: "pkgcell", "data-label": "Package", text: d.name }),
            el("td", { "data-label": "Version" }, d.version ? el("span", { class: "mono", text: d.version }) : "unresolved"),
            el("td", { "data-label": "Source", text: d.source }),
            el("td", { "data-label": "Declared", text: d.direct === null ? "unknown" : d.direct ? "direct" : "transitive" }),
            el("td", { "data-label": "OSV", text: d.osv_status.replace("_", " ") }),
          ),
        ),
      ),
    ),
  );

  return el(
    "section",
    { class: "method", "aria-labelledby": "method-h" },
    el("div", { class: "section-head" }, el("h2", { id: "method-h", class: "section-title", text: "Method and limitations" })),
    el(
      "ol",
      { class: "notes" },
      el(
        "li",
        { id: "fn-resolution" },
        el("h3", { text: "How versions were resolved" }),
        el("p", { text: resolution }),
        r.notes.length > 0 && el("ul", { class: "bullets" }, r.notes.map((n) => el("li", { text: n }))),
      ),
      el("li", { id: "fn-scope" }, el("h3", { text: "What was and was not examined" }), el("ul", { class: "bullets" }, report.limitations.map((n) => el("li", { text: n })))),
      el(
        "li",
        { id: "fn-verdicts" },
        el("h3", { text: "How a verdict is decided" }),
        el("p", {
          text: "likely reachable needs a call path from the repository’s own code to the vulnerable function, every step a file and line found by the code index. uncertain means the evidence was missing or a step failed; it never defaults to safe. no reachable path detected means none was found within the scope stated for that finding, which is not proof of safety.",
        }),
      ),
    ),
    el(
      "details",
      { class: "details" },
      el("summary", {}, icon("icon-chevron", "icon icon--sm"), `All ${report.dependencies.length} dependencies`),
      depsTable,
    ),
  );
}

export function renderSignoff(report) {
  return el(
    "footer",
    { class: "signoff" },
    el(
      "div",
      { class: "signoff__sig" },
      el("div", {}, `ProveItReachable ${report.tool_version}`, el("span", { class: "label", text: "Issued by" })),
      el("div", {}, formatDateTime(report.generated_at), el("span", { class: "label", text: "Issued on" })),
      el("div", {}, report.model, el("span", { class: "label", text: "Reasoning model" })),
      el("div", {}, report.scan_id, el("span", { class: "label", text: "Scan id" })),
    ),
    el("p", {
      class: "signoff__text",
      text: "Automated analysis with no human review. Each verdict is bounded by the evidence printed beside it and by the limitations stated above; it is triage, not a security audit.",
    }),
  );
}

export function renderReportSkeleton() {
  return el(
    "div",
    { class: "report-skel", "aria-hidden": "true" },
    el("span", { class: "skel skel--num" }),
    el("span", { class: "skel skel--row" }),
    el("span", { class: "skel skel--row" }),
    el("span", { class: "skel skel--row" }),
  );
}
