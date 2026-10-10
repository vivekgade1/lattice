import dagre from "@dagrejs/dagre";

// Node sizes per kind (px at zoom 1). A node may override with w / h.
const SIZE = {
  rule: [200, 48],
  endpoint: [304, 48],
  table: [180, 48],
  service: [180, 48],
  machine: [180, 48],
  invariant: [232, 48],
  scenario: [232, 48],
  code: [208, 48],
  state: [148, 44],
  stage: [236, 60],
  caller: [148, 60],
};
export const sizeOf = (n) => {
  const [w, h] = SIZE[n.kind] || [208, 48];
  return { w: n.w || w, h: n.h || h };
};

const HEAD_H = 28; // column header band
const GROUP_PAD = 8; // inside a group container
const GROUP_HEAD = 24; // group label band
const ROW_GAP = 10;
const GROUP_GAP = 18;
const COL_GAP = 40;
export const WRAP_GAP = 32;
const GUTTER = 120; // layer names, left of each layer

/**
 * Layered layout by dagre. Returns { pos: {id: {x, y}}, extras: [] }.
 */
function dagreLayout(nodes, edges, direction) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: direction === "TB" ? "TB" : "LR", nodesep: 24, ranksep: 64, marginx: 16, marginy: 16 });
  g.setDefaultEdgeLabel(() => ({}));
  const ids = new Set(nodes.map((n) => n.id));
  for (const n of nodes) {
    const { w, h } = sizeOf(n);
    g.setNode(n.id, { width: w, height: h });
  }
  for (const e of edges) {
    if (ids.has(e.source) && ids.has(e.target) && e.source !== e.target) g.setEdge(e.source, e.target);
  }
  dagre.layout(g);
  const pos = {};
  for (const n of nodes) {
    const p = g.node(n.id);
    const { w, h } = sizeOf(n);
    pos[n.id] = { x: Math.round(p.x - w / 2), y: Math.round(p.y - h / 2) };
  }
  return { pos, extras: [] };
}

/**
 * Wrapped rows, in the given order (a process read left to right, then the next row).
 * `perRow` comes from the canvas width.
 */
function wrapLayout(nodes, perRow) {
  const pos = {};
  const row = {};
  const cols = Math.max(1, perRow);
  let y = 16;
  for (let i = 0; i < nodes.length; i += cols) {
    const slice = nodes.slice(i, i + cols);
    const h = Math.max(...slice.map((n) => sizeOf(n).h));
    let x = 16;
    slice.forEach((n) => {
      const s = sizeOf(n);
      pos[n.id] = { x, y: y + Math.round((h - s.h) / 2) };
      row[n.id] = i / cols;
      x += s.w + WRAP_GAP;
    });
    y += h + 56;
  }
  return { pos, extras: [], row };
}

/**
 * Columns: node.column (0..n) picks the column, node.group (optional) puts consecutive
 * nodes of a column in a labelled container. `columns` = [{label}] heads each column.
 * The column holding groups keeps its given order (the anchor); every other column is
 * ordered by the mean y of its neighbours already placed (short lines) and stacked from
 * the top.
 */
function columnsLayout(nodes, edges, columns = [], groups = {}) {
  const byCol = new Map();
  for (const n of nodes) {
    const c = Number.isInteger(n.column) ? n.column : 0;
    if (!byCol.has(c)) byCol.set(c, []);
    byCol.get(c).push(n);
  }
  const colIds = [...byCol.keys()].sort((a, b) => a - b);
  const nbr = new Map(nodes.map((n) => [n.id, []]));
  for (const e of edges) {
    if (nbr.has(e.source) && nbr.has(e.target)) {
      nbr.get(e.source).push(e.target);
      nbr.get(e.target).push(e.source);
    }
  }
  // x per column
  const colX = {};
  const colW = {};
  let x = 16;
  for (const c of colIds) {
    colW[c] = Math.max(...byCol.get(c).map((n) => sizeOf(n).w)) + (byCol.get(c).some((n) => n.group) ? GROUP_PAD * 2 : 0);
    colX[c] = x;
    x += colW[c] + COL_GAP;
  }
  const pos = {};
  const extras = [];
  const top = (columns.length ? HEAD_H + 12 : 0) + 16;

  const stack = (c, list) => {
    let y = top;
    let lastGroup = null;
    let box = null;
    const hasGroups = list.some((n) => n.group);
    const inner = hasGroups ? GROUP_PAD : 0;
    for (const n of list) {
      const s = sizeOf(n);
      if (n.group !== lastGroup) {
        if (box) {
          box.h = y - box.y - ROW_GAP + GROUP_PAD;
          extras.push(box);
          y += GROUP_PAD + GROUP_GAP - ROW_GAP;
        }
        box = null;
        lastGroup = n.group;
        if (n.group) {
          const g = groups[n.group] || { label: n.group };
          box = { id: `group:${c}:${n.group}`, type: "group", x: colX[c], y, w: colW[c], label: g.label, count: 0 };
          y += GROUP_HEAD;
        }
      }
      if (box) box.count += 1;
      pos[n.id] = { x: colX[c] + inner, y };
      y += s.h + ROW_GAP;
    }
    if (box) {
      box.h = y - box.y - ROW_GAP + GROUP_PAD;
      extras.push(box);
    }
  };

  const anchor = colIds.find((c) => byCol.get(c).some((n) => n.group)) ?? colIds[Math.floor(colIds.length / 2)];
  if (anchor !== undefined) stack(anchor, byCol.get(anchor));
  // then outward: nearest columns first
  const rest = colIds.filter((c) => c !== anchor).sort((a, b) => Math.abs(a - anchor) - Math.abs(b - anchor) || a - b);
  for (const c of rest) {
    const list = byCol.get(c).map((n, i) => {
      const ys = nbr.get(n.id).filter((m) => pos[m]).map((m) => pos[m].y);
      return { n, i, key: ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : Infinity };
    });
    list.sort((a, b) => a.key - b.key || a.i - b.i);
    // stacked from the top in that order, so the first view shows every column's head
    let y = top;
    for (const { n } of list) {
      pos[n.id] = { x: colX[c], y };
      y += sizeOf(n).h + ROW_GAP;
    }
  }
  columns.forEach((col, i) => {
    if (col && byCol.has(i)) extras.push({ id: `head:${i}`, type: "colhead", x: colX[i], y: 12, w: colW[i], h: HEAD_H, label: col.label });
  });
  return { pos, extras };
}

/**
 * Layers, top to bottom: node.column picks the layer, `columns` = [{label}] heads each one.
 * A layer's nodes run left to right and wrap at the canvas width, ordered by the mean x of
 * their neighbours in the layers above.
 */
function layersLayout(nodes, edges, columns = [], width = 900) {
  const W = Math.max(360, width);
  const byLayer = new Map();
  for (const n of nodes) {
    const c = Number.isInteger(n.column) ? n.column : 0;
    if (!byLayer.has(c)) byLayer.set(c, []);
    byLayer.get(c).push(n);
  }
  const nbr = new Map(nodes.map((n) => [n.id, []]));
  for (const e of edges) {
    if (nbr.has(e.source) && nbr.has(e.target)) {
      nbr.get(e.source).push(e.target);
      nbr.get(e.target).push(e.source);
    }
  }
  const pos = {};
  const extras = [];
  const layer = {};
  let y = 12;
  for (const c of [...byLayer.keys()].sort((a, b) => a - b)) {
    const list = byLayer.get(c).map((n, i) => {
      const xs = nbr.get(n.id).filter((m) => pos[m]).map((m) => pos[m].x);
      return { n, i, key: xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : Infinity };
    });
    list.sort((a, b) => a.key - b.key || a.i - b.i);
    // the layer's name sits in a left gutter, on its first row
    if (columns[c]) extras.push({ id: `head:${c}`, type: "colhead", x: 16, y: y + 12, w: GUTTER - 24, h: HEAD_H, label: columns[c].label, side: true });
    const x0 = columns.length ? 16 + GUTTER : 16;
    let x = x0;
    let rowH = 0;
    for (const { n } of list) {
      const s = sizeOf(n);
      if (x > x0 && x + s.w > W - 16) {
        x = x0;
        y += rowH + ROW_GAP;
        rowH = 0;
      }
      pos[n.id] = { x, y };
      layer[n.id] = c;
      x += s.w + 16;
      rowH = Math.max(rowH, s.h);
    }
    y += rowH + 24;
  }
  return { pos, extras, layer };
}

/** { pos: {id: {x, y}}, extras: [{id, type: group|colhead, x, y, w, h, label}], row? } */
export function layout(nodes, edges, { mode = "dagre", direction = "LR", columns, groups, perRow = 3, width = 900 } = {}) {
  if (mode === "columns") return columnsLayout(nodes, edges, columns, groups);
  if (mode === "layers") return layersLayout(nodes, edges, columns, width);
  if (mode === "wrap") return wrapLayout(nodes, perRow);
  return dagreLayout(nodes, edges, direction);
}
