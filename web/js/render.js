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

export function secondsBetween(a, b) {
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
      // The API message already names the problem, so it leads on its own; a title is only for client-side notices.
      options.title && el("p", { class: "notice__title", text: options.title }),
      el("p", { class: options.title ? "notice__msg" : "notice__lead", text: error.message }),
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
        { "aria-hidden": "true" },
        el(
          "div",
          { class: "demo demo--skel" },
          el("div", {}, el("span", { class: "skel skel--title" }), el("span", { class: "skel skel--line" })),
          el("span", { class: "skel skel--btn" }),
        ),
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
      {},
      banner({ code: "", message }, { title: "The demo list did not load" }),
      el("button", { class: "btn", type: "button", text: "Try again", on: { click: onRetry } }),
    ),
  );
}

/** One teaser chip per non-zero verdict, from the demo's recorded `by_verdict` counts. */
function demoResult(by) {
  if (!by) return null;
  const parts = [
    ["likely reachable", "mark-reach"],
    ["uncertain", "mark-uncertain"],
    ["no reachable path detected", "mark-none"],
  ].filter(([verdict]) => by[verdict] > 0);
  if (parts.length === 0) return el("span", { class: "demo__result" }, el("span", { text: "Clean: no findings" }));
  return el(
    "span",
    { class: "demo__result" },
    parts.map(([verdict, mark]) => el("span", {}, icon(mark, "icon icon--sm"), `${by[verdict]} ${verdict}`)),
  );
}

export function renderDemos(list, demos, onRun) {
  list.setAttribute("aria-busy", "false");
  clear(list);
  for (const demo of demos) {
    list.append(
      el(
        "li",
        {},
        el(
          "button",
          {
            class: "demo",
            type: "button",
            "aria-label": `Run demo: ${demo.title}`,
            dataset: { demo: demo.slug },
            on: { click: () => onRun(demo) },
          },
          el(
            "span",
            { class: "demo__main" },
            el("span", { class: "demo__name" }, el("span", { text: demo.title }), el("span", { class: "tag", text: "placeholder" })),
            el("span", { class: "demo__desc", text: demo.description }),
            demoResult(demo.by_verdict),
          ),
          el("span", { class: "demo__go" }, "Run demo", icon("icon-go", "icon icon--sm")),
        ),
      ),
    );
  }
}

export function setDemosDisabled(list, disabled) {
  for (const button of list.querySelectorAll("button[data-demo]")) button.disabled = disabled;
}

/* ------------------------------------------------------------ progress feed */

/** [key, row label, short label, the tool behind the stage]. The tool is shown beside every stage. */
export const STAGES = [
  ["resolve", "Resolve dependencies", "Resolve", "Lockfile · uv"],
  ["osv", "Find known advisories", "Advisories", "OSV"],
  ["research", "Research the vulnerable function", "Research", "Tavily"],
  ["analysis", "Index your code", "Analysis", "Static analysis · Sandbox"],
  ["reasoning", "Reason over the evidence", "Reasoning", "Nemotron"],
  ["report", "Rank and write verdicts", "Verdicts", "Verdict rules"],
];
const NODE_ICON = { running: "stamp-running", done: "stamp-done", failed: "stamp-failed", skipped: "stamp-skipped" };
const STATE_WORD = { pending: "Pending", running: "Running", done: "Done", failed: "Failed", skipped: "Skipped" };

function fillNode(node, state) {
  clear(node);
  if (NODE_ICON[state]) node.append(icon(NODE_ICON[state]));
  node.append(el("span", { class: "sr-only", text: STATE_WORD[state] || state }));
}

export function buildTrack(track) {
  clear(track);
  for (const [key, label, , tool] of STAGES) {
    const node = el("span", { class: "stage__node" });
    fillNode(node, "pending");
    track.append(
      el(
        "li",
        { class: "stage", dataset: { step: key, state: "pending" } },
        node,
        el("p", { class: "stage__name", text: label }),
        el("p", { class: "stage__tool" }, el("span", { class: "tool", text: tool })),
        el("p", { class: "stage__msg" }),
        el("p", { class: "stage__time" }),
      ),
    );
  }
}

export function stageLabel(step) {
  const found = STAGES.find(([key]) => key === step);
  return found ? found[1] : step;
}

/** Move one stage to a state. `stamp` replays the settle motion (CSS drops it for reduced motion). */
export function setStage(track, step, state, message, seconds, stamp = false) {
  const li = track.querySelector(`[data-step="${step}"]`);
  if (!li) return;
  const changed = li.dataset.state !== state;
  li.dataset.state = state;
  fillNode(li.querySelector(".stage__node"), state);
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

/** Counts read from the DOM, so the label and meter always agree with what the rows show. */
export function trackProgress(track) {
  const rows = [...track.querySelectorAll(".stage")];
  const settled = rows.filter((li) => ["done", "failed", "skipped"].includes(li.dataset.state)).length;
  const index = rows.findIndex((li) => li.dataset.state === "running");
  return {
    total: rows.length,
    settled,
    current: index >= 0 ? { index, label: stageLabel(rows[index].dataset.step) } : null,
  };
}

/* ---------------------------------------------------------------- scan head */

function metaItem(label, value) {
  return el("div", { class: "meta__item" }, el("dt", { text: label }), el("dd", {}, value));
}

/**
 * The repository identity: owner/name as the page heading, then the facts the scan knows so far.
 * @param {HTMLElement} container
 * @param {{ name: string, source: string, commit?: string|null, branch?: string, resolvedBy?: string, model?: string }} info
 */
export function renderScanHead(container, info) {
  clear(container);
  const slash = info.name.indexOf("/");
  const owner = slash > 0 ? info.name.slice(0, slash + 1) : "";
  const repo = slash > 0 ? info.name.slice(slash + 1) : info.name;
  container.append(
    el("h1", { id: "scan-h", class: "scan-head__name", tabindex: "-1" }, owner && el("span", { class: "owner", text: owner }), repo),
  );
  const meta = el("dl", { class: "meta" });
  meta.append(metaItem("Source", info.source));
  meta.append(
    metaItem("Commit", info.commit ? el("span", { class: "mono", text: info.commit }) : el("span", { class: "pending-text", text: "resolved when the scan starts" })),
  );
  if (info.branch) meta.append(metaItem("Branch", el("span", { class: "mono", text: info.branch })));
  if (info.resolvedBy) meta.append(metaItem("Versions", info.resolvedBy));
  if (info.model) meta.append(metaItem("Model", el("span", { class: "mono", text: info.model })));
  container.append(meta);
}

/* ------------------------------------------------------------------ summary */

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

const STRIP_MAX = 60;

/** One mark per finding in rank order: the funnel as a row of shapes, never colour alone. */
function advisoryStrip(report) {
  const shown = report.findings.slice(0, STRIP_MAX);
  const strip = el("div", { class: "strip", "aria-hidden": "true" });
  shown.forEach((f, i) => {
    const meta = VERDICTS[f.verdict] || VERDICTS["no reachable path detected"];
    const item = el("span", { class: "strip__item" }, icon(meta.mark, "icon icon--strip"));
    item.style.setProperty("--i", String(i));
    strip.append(item);
  });
  if (report.findings.length > STRIP_MAX) strip.append(el("span", { class: "hint", text: `+${report.findings.length - STRIP_MAX} more` }));
  return strip;
}

/** Glue the middle dot to the word before it, so a wrapped line never starts with "·". */
const keepDot = (text) => text.replace(" · ", " · ");

function pipelineStrip(report) {
  const steps = report.steps || [];
  const first = steps.find((s) => s.started_at);
  const last = [...steps].reverse().find((s) => s.finished_at);
  const total = first && last ? secondsBetween(first.started_at, last.finished_at) : null;
  return el(
    "div",
    { class: "funnel__pipeline" },
    el(
      "div",
      { class: "pipeline__head" },
      el("h3", { class: "pipeline__title", text: "How this report was made" }),
      total !== null && el("p", { class: "pipeline__total", text: `Recorded run time ${formatSeconds(total)}` }),
    ),
    el(
      "ol",
      { class: "pipe", "aria-label": "Pipeline steps" },
      steps.map((step) => {
        const stage = STAGES.find(([key]) => key === step.name);
        const node = el("span", { class: "stage__node" });
        fillNode(node, step.state);
        const seconds = secondsBetween(step.started_at, step.finished_at);
        return el(
          "li",
          { class: "pipe__item", dataset: { state: step.state } },
          node,
          el("p", { class: "pipe__name", text: stage ? stage[2] : step.name }),
          el("p", { class: "pipe__tool", text: stage ? keepDot(stage[3]) : "" }),
          seconds !== null && el("p", { class: "pipe__time", text: formatSeconds(seconds) }),
        );
      }),
    ),
  );
}

/**
 * The summary: "N advisories -> M likely reachable" as the headline, a reading, the three counts,
 * one mark per finding, and the pipeline that produced it. Returns { node, animate(reduceMotion) }.
 */
export function renderSummary(report, { isMock }) {
  const s = report.summary;
  const by = s.by_verdict;
  const lr = by["likely reachable"];
  const un = by.uncertain;
  const np = by["no reachable path detected"];
  const advisories = report.findings.filter((f) => f.kind === "advisory").length;
  const total = s.findings_total;

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
        { class: "funnel__line", "aria-hidden": "true" },
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
    if (un > 0) parts.push(`${un} ${plural(un, "is", "are")} uncertain: read the reason shown with ${plural(un, "it", "each")}.`);
    reading = el("p", { class: "funnel__reading" }, parts.join(" "));
    if (np > 0) {
      reading.append(` ${np} had no reachable path detected within the scope stated in `, el("a", { href: "#fn-scope", text: "the method notes" }), ".");
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
      el("caption", { text: "Verdicts" }),
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
        { class: "funnel__line", "aria-hidden": "true" },
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
      class: "funnel__reading",
      text: isClean
        ? `No known-vulnerable dependencies were found. All ${s.dependencies_checked} dependencies were checked against OSV at their exact versions.`
        : `Not a clean result. ${s.dependencies_checked} of ${s.dependencies_total} dependencies were checked and have no known advisories. ${s.dependencies_unresolved} could not be resolved to an exact version, so ${plural(s.dependencies_unresolved, "it was", "they were")} not examined: see the list below.`,
    });
    tally = el(
      "table",
      { class: "tally" },
      el("caption", { text: "Dependencies" }),
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

  const heading = report.outcome === "clean" ? "Summary: clean" : report.outcome === "incomplete" ? "Summary: incomplete" : "Summary";
  const node = el(
    "section",
    { class: `funnel funnel--${report.outcome}`, "aria-labelledby": "funnel-h", dataset: { outcome: report.outcome } },
    el("div", { class: "funnel__top" }, el("h2", { id: "funnel-h", class: "funnel__title", text: heading }), isMock && el("span", { class: "tag", text: "Demo data" })),
    el(
      "div",
      { class: "funnel__body" },
      el("div", {}, figure, report.outcome === "findings" && report.findings.length > 0 && advisoryStrip(report), reading),
      tally,
    ),
    pipelineStrip(report),
  );
  return { node, animate };
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

/** Which tool produced a source link: Tavily research is credited visibly on every finding. */
function sourceBadge(source) {
  if (source === "tavily") return el("span", { class: "src src--tavily" }, icon("icon-search", "icon icon--sm"), "Tavily research");
  if (source === "osv") return el("span", { class: "src", text: "OSV" });
  return el("span", { class: "src", text: String(source) });
}

function sourceNote(links) {
  const tavily = links.filter((l) => l.source === "tavily").length;
  const text = `${links.length} ${plural(links.length, "source", "sources")}${tavily > 0 ? `, ${tavily} found by Tavily research` : ""}`;
  return el("p", { class: "src-note", text });
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
        f.advisory_links.length > 0 && sourceNote(f.advisory_links),
        el(
          "ul",
          { class: "links" },
          f.advisory_links.map((link) =>
            el(
              "li",
              {},
              el("p", { class: "links__title" }, safeLink(link.url, link.title || link.url)),
              el("p", { class: "links__meta" }, sourceBadge(link.source), link.title && el("code", { class: "links__url", text: link.url })),
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

function renderFinding(f) {
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
    { class: "results__head cols", "aria-hidden": "true" },
    ...["Verdict", "Package", "Advisory", "Where", "Confidence"].map((t) => el("span", { text: t })),
    el("span"),
  );
  const list = el("ol", { class: "results" }, report.findings.map((f) => renderFinding(f)));
  const node = el(
    "section",
    { class: "results-section", "aria-labelledby": "results-h" },
    el(
      "div",
      { class: "section-head" },
      el(
        "div",
        {},
        el("h2", { id: "results-h", class: "block-h" }, "Findings", el("span", { class: "section-count", text: String(report.findings.length) })),
        el("p", { class: "section-sub", text: "Likely reachable first, then uncertain, then no reachable path detected; higher confidence first within each. Open a finding for its evidence." }),
      ),
    ),
    el("div", { class: "results-wrap" }, head, list),
  );
  // The top-ranked finding opens first, with its evidence beneath the row.
  const first = list.querySelector(".finding__toggle");
  if (first) setOpen(first, list.querySelector(".finding__body"), true);
  return node;
}

/* ------------------------------------------------- unresolved, method, footer */

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
        el("h2", { id: "notexam-h", class: "block-h", text: "Unresolved and unchecked dependencies" }),
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

function pipelineLog(report) {
  return el(
    "ol",
    { class: "log" },
    report.steps.map((step) => {
      const stage = STAGES.find(([key]) => key === step.name);
      const seconds = secondsBetween(step.started_at, step.finished_at);
      return el(
        "li",
        {},
        el("p", {}, el("strong", { text: stage ? stage[1] : step.name }), " ", el("span", { class: "tool", text: stage ? stage[3] : "" }), " ", el("span", { class: "hint", text: STATE_WORD[step.state] || step.state })),
        el("p", { class: "log__msg", text: step.message || "No message recorded." }),
        el("p", { class: "hint", text: seconds === null ? "" : formatSeconds(seconds) }),
      );
    }),
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
    { class: "method panel", "aria-labelledby": "method-h" },
    el("div", { class: "section-head" }, el("h2", { id: "method-h", class: "block-h", text: "Method and limitations" })),
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
    el("details", { class: "details" }, el("summary", {}, icon("icon-chevron", "icon icon--sm"), "Pipeline log"), pipelineLog(report)),
    el("details", { class: "details" }, el("summary", {}, icon("icon-chevron", "icon icon--sm"), `All ${report.dependencies.length} dependencies`), depsTable),
  );
}

export function renderReportFoot(report) {
  return el(
    "footer",
    { class: "report-foot" },
    el(
      "div",
      { class: "report-foot__facts" },
      el("p", {}, "Generated by ", el("span", { text: `ProveItReachable ${report.tool_version}` })),
      el("p", {}, "On ", el("span", { text: formatDateTime(report.generated_at) })),
      el("p", {}, "Model ", el("span", { class: "mono", text: report.model })),
      el("p", {}, "Scan ", el("span", { class: "mono", text: report.scan_id })),
    ),
    el("p", {
      class: "report-foot__text",
      text: "Automated analysis with no human review. Each verdict is bounded by the evidence shown beside it and by the limitations stated above; it is triage, not a security audit.",
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
