// Evidence > Checks: the last check's findings with the counterexample, each linked to the
// rule or element it is about. Empty state invites a run.

import { h, icon, add, fmtTime, plural } from "../util.js";
import { state } from "../store.js";
import { targetChip } from "../targets.js";
import { rulesByTarget } from "../overlay.js";

const LAYER_WORD = { A: "Layer A", B: "Layer B", C: "Layer C", D: "Layer D", X: "Layer X" };

function runCheck() {
  const btn = document.querySelector("#top-actions .btn-primary");
  if (btn && !btn.disabled) btn.click();
}

export function render(model, root, ctx) {
  const lc = model.app?.last_check;
  add(root, h("header", { class: "view-head" },
    h("h1", {}, "Checks"),
    h("p", { class: "lede" }, "What the last run of the spec against the app found. Each finding is a counterexample: the request, what the spec expects, and what the app did."),
  ));
  if (!lc) {
    const configured = !!model.app?.configured;
    add(root, h("div", { class: "chk-empty" },
      h("h2", {}, "Run a check"),
      h("p", { class: "muted" }, configured
        ? "No check has run yet. A check replays the spec's scenarios against the app and reports where it disagrees."
        : "No app is hooked up (studio.toml), so there is nothing to check against."),
      configured
        ? h("button", { type: "button", class: "btn btn-primary", disabled: state.checkRunning, onclick: runCheck },
            icon("play"), state.checkRunning ? "Checking…" : "Run check")
        : null,
    ));
    return;
  }
  const findings = lc.findings || [];
  const pass = lc.verdict === "pass";
  const counts = Object.entries(lc.counts || {}).filter(([, n]) => n);
  add(root, h("div", { class: "chk-summary" },
    h("span", { class: ["pill", pass ? "pill-pass" : "pill-fail"], role: "status" },
      icon(pass ? "check" : "diamond"), pass ? "Pass" : `Fail · ${plural(findings.length, "finding")}`),
    h("span", { class: "muted" }, `Run ${fmtTime(lc.at)}`),
    counts.length ? h("span", { class: "muted" }, `By layer: ${counts.map(([k, n]) => `${k} ${n}`).join(" · ")}`) : null,
    h("button", { type: "button", class: "btn", disabled: state.checkRunning, onclick: runCheck }, icon("play"), "Run again"),
  ));
  if (!findings.length) {
    add(root, h("p", { class: "s2-empty" },
      pass ? `Every check passed${lc.targets ? ` (${plural(Object.keys(lc.targets).length, "check")})` : ""}.` : "The check failed without a finding."));
    return;
  }
  const idx = rulesByTarget(model);
  add(root, h("ol", { class: "chk-list", "aria-label": "Findings" }, findings.map((f) => {
    const rules = idx.get(f.target) || [];
    return h("li", { class: "chk-item" },
      h("div", { class: "chk-item-head" },
        h("span", { class: "chk-layer", title: LAYER_WORD[f.layer] || f.layer }, f.layer || "?"),
        h("span", { class: "s2-trust trust-bad" }, icon("diamond"), h("span", {}, "Fails")),
        f.target ? targetChip(f.target) : null,
        rules.slice(0, 6).map((r) => targetChip(`rule:${r}`)),
      ),
      h("p", { class: "chk-counter" }, f.message || ""),
      rules.length
        ? h("div", { class: "chk-actions" },
            h("button", { type: "button", class: "btn btn-quiet", onclick: () => ctx.openCode(`rule:${rules[0]}`) }, icon("text-lines"), `Open the code for ${rules[0]}`),
            h("button", { type: "button", class: "btn btn-quiet", onclick: () => ctx.askAbout(f.target) }, icon("chat"), "Ask about this"))
        : h("div", { class: "chk-actions" },
            f.target ? h("button", { type: "button", class: "btn btn-quiet", onclick: () => ctx.askAbout(f.target) }, icon("chat"), "Ask about this") : null),
    );
  })));
}
