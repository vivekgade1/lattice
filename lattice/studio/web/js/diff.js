// A styled unified diff: one row per line, with old/new line numbers and +/- marks that
// do not rely on colour alone.

import { h } from "./util.js";

export function unifiedDiff(text, opts = {}) {
  const rows = [];
  let oldNo = 0;
  let newNo = 0;
  for (const line of String(text || "").replace(/\n$/, "").split("\n")) {
    let cls = "ctx";
    let a = "";
    let b = "";
    let mark = " ";
    let body = line;
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(line);
    if (line.startsWith("--- ") || line.startsWith("+++ ") || line.startsWith("diff ") || line.startsWith("index ")) {
      cls = "meta";
      mark = "";
    } else if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      cls = "hunk";
      mark = "";
      body = `Lines ${hunk[1]}–${hunk[2]}${hunk[3] ? " ·" + hunk[3] : ""}`;
    } else if (line.startsWith("+")) {
      cls = "add";
      mark = "+";
      body = line.slice(1);
      b = newNo++;
    } else if (line.startsWith("-")) {
      cls = "del";
      mark = "−";
      body = line.slice(1);
      a = oldNo++;
    } else {
      body = line.startsWith(" ") ? line.slice(1) : line;
      a = oldNo++;
      b = newNo++;
    }
    rows.push(
      h(
        "div",
        { class: ["diff-line", `diff-${cls}`] },
        h("span", { class: "diff-no", "aria-hidden": "true" }, a === "" ? "" : String(a)),
        h("span", { class: "diff-no", "aria-hidden": "true" }, b === "" ? "" : String(b)),
        h("span", { class: "diff-mark", "aria-label": cls === "add" ? "added" : cls === "del" ? "removed" : null }, mark),
        h("span", { class: "diff-text" }, body || " "),
      ),
    );
  }
  return h("div", { class: ["diff", opts.className], role: "region", "aria-label": opts.label || "Diff", tabindex: "0" }, rows);
}
