// A small, safe markdown renderer for the owner's REQUIREMENTS.md. It keeps the source
// line of every block, so a rule (which the trace places at a line) can be attached to
// the block that starts there. All text is escaped before inline formatting.

import { esc } from "./util.js";

/** Inline markdown -> HTML (escaped): `code`, **bold**, *em*, [text](url). */
export function inline(text) {
  // Code spans first, as placeholders, so **bold `code`** still pairs up.
  const codes = [];
  let t = String(text).replace(/`([^`]*)`/g, (_, c) => {
    codes.push(`<code>${esc(c)}</code>`);
    return `\u0000${codes.length - 1}\u0000`;
  });
  t = esc(t);
  t = t.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>");
  t = t.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" rel="noopener" target="_blank">$1</a>');
  return t.replace(/\u0000(\d+)\u0000/g, (_, i) => codes[Number(i)]);
}

/**
 * Parse markdown into blocks:
 *   {type:"heading", level, text, line}
 *   {type:"para", text, line}
 *   {type:"list", ordered, items:[{text, line, children: list|null}]}
 *   {type:"code", text, line}
 *   {type:"hr", line}
 * Lines are 1-based.
 */
export function parse(md) {
  const lines = String(md).replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  const itemRe = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

  function parseList(baseIndent) {
    const ordered = /\d/.test(itemRe.exec(lines[i])[2]);
    const list = { type: "list", ordered, items: [], line: i + 1 };
    while (i < lines.length) {
      const m = itemRe.exec(lines[i]);
      if (!m) {
        // A continuation line (indented, non-empty) joins the last item.
        if (lines[i].trim() && /^\s+/.test(lines[i]) && list.items.length) {
          list.items[list.items.length - 1].text += " " + lines[i].trim();
          i++;
          continue;
        }
        break;
      }
      const indent = m[1].length;
      if (indent < baseIndent) break;
      if (indent > baseIndent && list.items.length) {
        const last = list.items[list.items.length - 1];
        last.children = parseList(indent);
        continue;
      }
      list.items.push({ text: m[3], line: i + 1, children: null });
      i++;
    }
    return list;
  }

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    if (/^```/.test(line)) {
      const start = i + 1;
      const body = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
      i++;
      blocks.push({ type: "code", text: body.join("\n"), line: start });
      continue;
    }
    const hm = /^(#{1,6})\s+(.*)$/.exec(line);
    if (hm) {
      blocks.push({ type: "heading", level: hm[1].length, text: hm[2], line: i + 1 });
      i++;
      continue;
    }
    if (/^(-{3,}|\*{3,})\s*$/.test(line)) {
      blocks.push({ type: "hr", line: i + 1 });
      i++;
      continue;
    }
    if (itemRe.test(line)) {
      blocks.push(parseList(itemRe.exec(line)[1].length));
      continue;
    }
    const start = i + 1;
    const body = [];
    while (i < lines.length && lines[i].trim() && !itemRe.test(lines[i]) && !/^(#{1,6})\s/.test(lines[i]) && !/^```/.test(lines[i])) {
      body.push(lines[i].trim());
      i++;
    }
    blocks.push({ type: "para", text: body.join(" "), line: start });
  }
  return blocks;
}

export function slugify(text) {
  return String(text)
    .toLowerCase()
    .replace(/<[^>]+>/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}
