/*
 * Props (all optional except nodes/edges):
 *   nodes:  [{ id, kind, label, sub?, trust?, trustLabel?, badges?: [{label, tone}], more?: {label, title},
 *              target?, column?, group?, w?, h? }]
 *           kind:  rule | endpoint | table | service | machine | invariant | scenario | code
 *                  | state | stage | caller | plan
 *           plan:  (kind "plan" only) { tier: fast|mid|frontier, status: pending|running|passed|gave_up|
 *                  blocked|merge_conflict|regressed|stopped, statusLabel?, rules?: "5 rules", facts?: "12k tokens" }
 *           trust: checked | unchecked | context | untraced | probe_gap | not_built | failing
 *           tone:  ok | warn | bad | gap | accent | neutral
 *   edges:  [{ id, source, target, kind?, label?, state? }]   state: active | failing | dashed
 *   layout: "dagre" (direction LR | TB) | "columns" (node.column, node.group) | "wrap"
 *           | "layers" (node.column = layer, top to bottom, wrapping at the canvas width)
 *   columns: [{label, sub?}]  groups: {id: {label}}
 *   readableZoom: the smallest zoom the first view may use (default 0.85: 13px labels
 *                 stay >= 11px); past it the view starts top-left and pans instead of fitting
 *   selected: [id], marked: [id]
 *   diff: { [id]: "modified" | "added" | "removed" | "impact" }
 *   onSelect(id, { additive }), onOpen(id), onExpand(id), onClear(), minimap?: boolean
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow, ReactFlowProvider, Handle, Position, MiniMap, Panel, useReactFlow,
} from "@xyflow/react";
import { layout, sizeOf, WRAP_GAP } from "./layout.js";
import { Icon } from "./icons.jsx";

// One colour, icon and word per trust state, the same as the rest of the app:
// probe gap amber + triangle, untraced violet + question, failing red + diamond.
const TRUST = {
  checked: { word: "checked", icon: "shield-check" },
  unchecked: { word: "not checked", icon: "text-lines" },
  context: { word: "context", icon: "info" },
  untraced: { word: "untraced", icon: "question" },
  probe_gap: { word: "probe gap", icon: "triangle" },
  not_built: { word: "not built", icon: "state" },
  failing: { word: "failing", icon: "diamond" },
};
const DIFF_WORD = { modified: "changed", added: "added", removed: "removed", impact: "impacted" };
const KINDS = ["rule", "endpoint", "table", "service", "machine", "invariant", "scenario", "code", "state", "stage", "caller", "plan"];
const READABLE = 0.85;

const Ctx = createContext(null);

function reducedMotion() {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/* ---------------------------------------------------------------- node renderers */

function Handles() {
  return (
    <>
      <Handle id="l" type="target" position={Position.Left} isConnectable={false} className="lg-handle" />
      <Handle id="t" type="target" position={Position.Top} isConnectable={false} className="lg-handle" />
      <Handle id="tr" type="target" position={Position.Right} isConnectable={false} className="lg-handle" />
      <Handle id="r" type="source" position={Position.Right} isConnectable={false} className="lg-handle" />
      <Handle id="b" type="source" position={Position.Bottom} isConnectable={false} className="lg-handle" />
      <Handle id="sl" type="source" position={Position.Left} isConnectable={false} className="lg-handle" />
      <Handle id="st" type="source" position={Position.Top} isConnectable={false} className="lg-handle" />
      <Handle id="tb" type="target" position={Position.Bottom} isConnectable={false} className="lg-handle" />
    </>
  );
}

function Shell({ data, label, glyph, sub }) {
  const { n, selected, marked, diff, tabbable } = data;
  const cb = useContext(Ctx);
  const trust = TRUST[n.trust];
  const kind = KINDS.includes(n.kind) ? n.kind : "rule";
  const { w, h } = sizeOf(n);
  const aria =
    `${n.kind} ${n.label}` + (trust ? `, ${n.trustLabel || trust.word}` : "") + (diff ? `, ${DIFF_WORD[diff] || diff}` : "") +
    (n.more ? `, ${n.more.label}` : "");
  const cls = [
    "lg-node", `lg-k-${kind}`,
    n.trust ? `lg-t-${n.trust}` : "",
    selected ? "is-selected" : "", marked ? "is-marked" : "", diff ? `lg-d-${diff}` : "",
  ].filter(Boolean).join(" ");
  const subText = sub !== undefined ? sub : n.sub;
  // a short label (a rule id, a state) leaves room for the trust mark beside it
  const trustUp = kind === "rule" || kind === "state";
  const trustMark = trust ? (
    <span className="lg-trust"><Icon name={trust.icon} size={12} />{n.trustLabel || trust.word}</span>
  ) : null;
  return (
    <>
      <Handles />
      <button
        type="button"
        className={cls}
        style={{ width: w, height: h }}
        tabIndex={tabbable ? 0 : -1}
        data-lg-id={n.id}
        data-target={n.target || undefined}
        aria-label={aria}
        aria-pressed={selected}
        title={n.sub ? `${n.label}: ${n.sub}` : n.label}
        onClick={(e) => cb.click(n.id, e)}
        onDoubleClick={(e) => { e.preventDefault(); cb.open(n.id); }}
        onKeyDown={(e) => cb.key(n.id, e)}
        onFocus={() => cb.focus(n.id)}
      >
        <span className="lg-row">
          {glyph && <Icon name={glyph} size={12} className="lg-glyph" />}
          <span className="lg-label">{label || n.label}</span>
          {trustUp && <span className="lg-fill" />}
          {trustUp && trustMark}
        </span>
        <span className="lg-row lg-meta">
          {!trustUp && trustMark}
          {diff && <span className="lg-diffword">{diff === "added" ? "+ " : diff === "removed" ? "− " : ""}{DIFF_WORD[diff] || diff}</span>}
          {subText && <span className="lg-sub">{subText}</span>}
          <span className="lg-fill" />
          {(n.badges || []).slice(0, 2).map((b, i) => (
            <span key={i} className={`lg-badge lg-tone-${b.tone || "neutral"}`}>{b.label}</span>
          ))}
          {n.more && (
            <span className="lg-more" data-lg-act="expand" title={n.more.title || n.more.label}>{n.more.label}</span>
          )}
        </span>
      </button>
    </>
  );
}

const METHOD = /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(.*)$/;
const RuleNode = ({ data }) => <Shell data={data} />;
const EndpointNode = ({ data }) => {
  const m = METHOD.exec(data.n.label);
  return <Shell data={data} label={m ? <><b className="lg-method">{m[1]}</b>{" "}{m[2]}</> : null} />;
};
const TableNode = ({ data }) => <Shell data={data} glyph="table" />;
const ServiceNode = ({ data }) => <Shell data={data} glyph="service" />;
const MachineNode = ({ data }) => <Shell data={data} glyph="machine" sub={data.n.sub || "lifecycle"} />;
const InvariantNode = ({ data }) => <Shell data={data} />;
const ScenarioNode = ({ data }) => <Shell data={data} glyph="scenario" />;
const CodeNode = ({ data }) => <Shell data={data} glyph="code" />;
const StateNode = ({ data }) => <Shell data={data} />;
const StageNode = ({ data }) => <Shell data={data} />;
const CallerNode = ({ data }) => <Shell data={data} glyph="caller" />;

/** A labelled container behind a group of nodes (a resource's endpoints). */
const GroupNode = ({ data }) => (
  <div className="lg-group" style={{ width: data.w, height: data.h }} aria-hidden="true">
    <span className="lg-group-label">{data.label}</span>
    {data.count > 1 && <span className="lg-group-n">{data.count}</span>}
  </div>
);
const HeadNode = ({ data }) => (
  <div className={"lg-colhead" + (data.side ? " is-side" : "")} style={{ width: data.w }} aria-hidden="true">
    {data.label}
    {data.sub && <span className="lg-colsub">{data.sub}</span>}
  </div>
);

/* A build-plan node: title, rule count and tier (icon + word), then the status (icon + word)
   and, once it ran, its tokens, time and lines. */
const PLAN_TIER = { fast: { word: "Fast", icon: "bolt" }, mid: { word: "Mid", icon: "bars" }, frontier: { word: "Frontier", icon: "star" } };
const PLAN_STATUS = {
  pending: { word: "Waiting", icon: "clock" },
  running: { word: "Running", icon: "spin" },
  passed: { word: "Passed", icon: "check" },
  gave_up: { word: "Gave up", icon: "cross" },
  blocked: { word: "Blocked", icon: "lock" },
  merge_conflict: { word: "Merge conflict", icon: "triangle" },
  regressed: { word: "Regressed", icon: "triangle" },
  stopped: { word: "Stopped", icon: "dash" },
};
const PlanNode = ({ data }) => {
  const { n, selected, marked, tabbable } = data;
  const cb = useContext(Ctx);
  const p = n.plan || {};
  const tier = PLAN_TIER[p.tier] || PLAN_TIER.mid;
  const st = PLAN_STATUS[p.status] || PLAN_STATUS.pending;
  const word = p.statusLabel || st.word;
  const { w, h } = sizeOf(n);
  const aria = `${n.label}, ${p.rules ? p.rules + ", " : ""}${tier.word} model, ${word}${p.facts ? ", " + p.facts : ""}`;
  const cls = ["lg-node", "lg-k-plan", `lg-ps-${p.status || "pending"}`, selected ? "is-selected" : "", marked ? "is-marked" : ""].filter(Boolean).join(" ");
  return (
    <>
      <Handles />
      <button
        type="button" className={cls} style={{ width: w, height: h }} tabIndex={tabbable ? 0 : -1}
        data-lg-id={n.id} aria-label={aria} aria-pressed={selected} title={n.sub ? `${n.label}: ${n.sub}` : n.label}
        onClick={(e) => cb.click(n.id, e)} onDoubleClick={(e) => { e.preventDefault(); cb.open(n.id); }}
        onKeyDown={(e) => cb.key(n.id, e)} onFocus={() => cb.focus(n.id)}
      >
        <span className="lg-row"><span className="lg-label">{n.label}</span></span>
        <span className="lg-row lg-meta">
          {p.rules && <span>{p.rules}</span>}
          <span className="lg-fill" />
          <span className="lg-tier"><Icon name={tier.icon} size={12} />{tier.word}</span>
        </span>
        <span className="lg-row lg-meta">
          <span className="lg-pstatus">
            {st.icon === "spin" ? <span className="lg-spin" aria-hidden="true" /> : <Icon name={st.icon} size={12} />}
            {word}
          </span>
          <span className="lg-fill" />
          {p.facts && <span className="lg-sub">{p.facts}</span>}
        </span>
      </button>
    </>
  );
};

const NODE_TYPES = {
  rule: RuleNode, endpoint: EndpointNode, table: TableNode, service: ServiceNode, machine: MachineNode,
  invariant: InvariantNode, scenario: ScenarioNode, code: CodeNode, state: StateNode, stage: StageNode,
  caller: CallerNode, plan: PlanNode, group: GroupNode, colhead: HeadNode,
};

/* ---------------------------------------------------------------- canvas */

function Canvas(props) {
  const {
    nodes = [], edges = [], direction = "LR", selected = [], marked = [], diff = {},
    minimap = false, handle, layout: mode = "dagre", columns, groups, readableZoom = READABLE,
  } = props;
  const rf = useReactFlow();
  const rootRef = useRef(null);
  const latest = useRef(props);
  latest.current = props;
  const reduceMotion = useMemo(reducedMotion, []);
  // no animation in a hidden tab either: its frames are suspended, so a transition would stall
  const still = () => reduceMotion || (typeof document !== "undefined" && document.hidden);
  const dragged = useRef(false);
  const [width, setWidth] = useState(0);
  const [rove, setRove] = useState(null);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => setWidth(Math.round(el.clientWidth / 20) * 20));
    ro.observe(el);
    setWidth(Math.round(el.clientWidth / 20) * 20);
    return () => ro.disconnect();
  }, []);

  const perRow = useMemo(() => {
    if (mode !== "wrap" || !nodes.length) return 1;
    const w = Math.max(...nodes.map((n) => sizeOf(n).w));
    return Math.max(1, Math.floor((Math.max(width, 320) - 32 + WRAP_GAP) / (w + WRAP_GAP)));
  }, [mode, nodes, width]);

  const sig = useMemo(
    () => [mode, direction, perRow, mode === "layers" ? width : 0, nodes.map((n) => `${n.id}@${n.column ?? ""}/${n.group ?? ""}`).join(","),
      edges.map((e) => `${e.source}>${e.target}`).join(",")].join("|"),
    [nodes, edges, direction, mode, perRow, width],
  );
  const laid = useMemo(
    () => layout(latest.current.nodes || [], latest.current.edges || [], { mode, direction, columns, groups, perRow, width }),
    [sig], // eslint-disable-line
  );
  const [over, setOver] = useState({ sig, map: {} });
  const overMap = over.sig === sig ? over.map : {};

  const selSet = useMemo(() => new Set(selected), [selected]);
  const markSet = useMemo(() => new Set(marked), [marked]);
  const idSet = useMemo(() => new Set(nodes.map((n) => n.id)), [nodes]);
  const tabId = rove && idSet.has(rove) ? rove : selected.find((x) => idSet.has(x)) || nodes[0]?.id;

  const rfNodes = useMemo(() => {
    const extras = laid.extras.map((x) => ({
      id: x.id, type: x.type, position: { x: x.x, y: x.y }, width: x.w, height: x.h,
      data: x, draggable: false, selectable: false, connectable: false, focusable: false,
      zIndex: x.type === "group" ? -1 : 0, className: "lg-extra",
    }));
    return extras.concat(nodes.map((n) => {
      const { w, h } = sizeOf(n);
      return {
        id: n.id,
        type: KINDS.includes(n.kind) ? n.kind : "rule",
        position: overMap[n.id] || laid.pos[n.id] || { x: 0, y: 0 },
        width: w, height: h,
        data: { n, selected: selSet.has(n.id), marked: markSet.has(n.id), diff: diff[n.id] || null, tabbable: n.id === tabId },
        draggable: true, selectable: false, connectable: false, focusable: false,
      };
    }));
  }, [nodes, laid, overMap, selSet, markSet, diff, tabId]);

  const rfEdges = useMemo(() => {
    const pos = (id) => overMap[id] || laid.pos[id];
    return edges
      .filter((e) => idSet.has(e.source) && idSet.has(e.target))
      .map((e) => {
        const hot = selSet.has(e.source) || selSet.has(e.target);
        const st = e.state === "failing" ? "failing" : e.state === "active" || hot ? "active" : e.state === "dashed" ? "dashed" : "";
        const a = pos(e.source), b = pos(e.target);
        let sh = "r", th = "l";
        if (mode === "wrap") {
          if (laid.row && laid.row[e.source] !== laid.row[e.target]) { sh = "b"; th = "t"; }
        } else if (mode === "dagre" && direction === "TB") {
          sh = "b"; th = "t";
        } else if (mode === "layers" && laid.layer) {
          const la = laid.layer[e.source], lb = laid.layer[e.target];
          if (la < lb) { sh = "b"; th = "t"; }
          else if (la > lb) { sh = "st"; th = "tb"; }
          else if (a && b && a.x > b.x) { sh = "sl"; th = "tr"; }
        } else if (a && b) {
          if (Math.abs(a.x - b.x) < 8) { sh = "r"; th = "tr"; }
          else if (a.x > b.x) { sh = "sl"; th = "tr"; }
        }
        const label = e.label || undefined;
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          sourceHandle: sh,
          targetHandle: th,
          type: "smoothstep",
          label,
          labelShowBg: true,
          labelBgPadding: [4, 2],
          className: ["lg-edge", st && `lg-edge-${st}`, e.state === "dashed" && "lg-edge-inferred", e.state === "active" && "lg-edge-flow",
            e.kind && `lg-edge-k-${String(e.kind).replace(/\W/g, "_")}`].filter(Boolean).join(" "),
          markerEnd: st === "failing" ? "lg-arrow-bad" : st === "active" ? "lg-arrow-accent" : "lg-arrow",
          zIndex: hot ? 1 : 0,
          focusable: false, selectable: false, interactionWidth: 8,
        };
      });
  }, [edges, idSet, selSet, laid, overMap, mode, direction]);

  const onNodesChange = useCallback(
    (changes) => {
      for (const c of changes) {
        if (c.type === "position" && c.position) {
          setOver((o) => {
            const base = o.sig === sig ? o.map : {};
            return { sig, map: { ...base, [c.id]: c.position } };
          });
        }
      }
    },
    [sig],
  );

  /** Bounds of everything drawn (nodes at their current place, plus group boxes and heads). */
  const bounds = useCallback(() => {
    const boxes = [];
    for (const n of latest.current.nodes || []) {
      const p = overMap[n.id] || laid.pos[n.id];
      if (p) { const s = sizeOf(n); boxes.push([p.x, p.y, p.x + s.w, p.y + s.h]); }
    }
    for (const x of laid.extras) boxes.push([x.x, x.y, x.x + x.w, x.y + (x.h || 0)]);
    if (!boxes.length) return null;
    return {
      minX: Math.min(...boxes.map((b) => b[0])), minY: Math.min(...boxes.map((b) => b[1])),
      maxX: Math.max(...boxes.map((b) => b[2])), maxY: Math.max(...boxes.map((b) => b[3])),
    };
  }, [laid, overMap]);

  const CTRL = 44; // the zoom controls' band at the bottom
  const PAD = 16;

  /** Everything on screen, however small the text gets (the owner asked for it). */
  const fit = useCallback(() => {
    const el = rootRef.current;
    const b = bounds();
    if (!el || !b) return;
    const W = el.clientWidth, H = el.clientHeight;
    const bw = b.maxX - b.minX + PAD * 2, bh = b.maxY - b.minY + PAD * 2;
    const z = Math.min(1, W / bw, (H - CTRL) / bh);
    rf.setViewport({ x: (W - (b.maxX - b.minX) * z) / 2 - b.minX * z, y: Math.max(PAD * z, (H - CTRL - (b.maxY - b.minY) * z) / 2) - b.minY * z, zoom: z },
      { duration: still() ? 0 : 200 });
  }, [rf, bounds]); // eslint-disable-line

  /** The first view: everything if it fits at a readable zoom, else top-left at a readable zoom. */
  const readable = useCallback(() => {
    const el = rootRef.current;
    const b = bounds();
    if (!el || !b) return;
    const W = el.clientWidth, H = el.clientHeight;
    if (!W || !H) return;
    const bw = b.maxX - b.minX + PAD * 2, bh = b.maxY - b.minY + PAD * 2;
    const fitZ = Math.min(1, W / bw, (H - CTRL) / bh);
    if (fitZ >= readableZoom) {
      const z = fitZ;
      rf.setViewport({ x: (W - (b.maxX - b.minX) * z) / 2 - b.minX * z, y: Math.max(PAD, (H - CTRL - (b.maxY - b.minY) * z) / 2) - b.minY * z, zoom: z }, { duration: 0 });
      return;
    }
    const z = Math.max(readableZoom, Math.min(1, W / bw));
    rf.setViewport({ x: PAD - b.minX * z + Math.max(0, (W - bw * z) / 2), y: PAD - b.minY * z, zoom: z }, { duration: 0 });
  }, [rf, bounds, readableZoom]);

  const inited = useRef(false);
  useEffect(() => { handle.fit = fit; handle.rf = rf; handle.readable = readable; });
  useEffect(() => {
    if (!width || !inited.current) return undefined;
    const id = setTimeout(readable, 0);
    return () => clearTimeout(id);
  }, [sig, width > 0]); // eslint-disable-line

  const focusNode = useCallback(
    (id) => {
      const root = rootRef.current;
      if (!root) return;
      setRove(id);
      const el = root.querySelector(`[data-lg-id="${CSS.escape(id)}"]`);
      if (!el) return;
      el.focus({ preventScroll: true });
      const r = el.getBoundingClientRect();
      const c = root.getBoundingClientRect();
      if (r.left < c.left || r.right > c.right || r.top < c.top || r.bottom > c.bottom) {
        const nd = rf.getNode(id);
        if (nd) {
          const s = sizeOf(nd.data.n);
          rf.setCenter(nd.position.x + s.w / 2, nd.position.y + s.h / 2, { zoom: rf.getZoom(), duration: still() ? 0 : 200 });
        }
      }
    },
    [rf],
  );

  const ctx = useMemo(
    () => ({
      click(id, e) {
        if (dragged.current) return;
        const L = latest.current;
        if (e.target.closest && e.target.closest('[data-lg-act="expand"]') && L.onExpand) {
          L.onExpand(id);
          return;
        }
        L.onSelect && L.onSelect(id, { additive: e.shiftKey || e.metaKey || e.ctrlKey });
      },
      open(id) {
        const L = latest.current;
        L.onOpen && L.onOpen(id);
      },
      focus(id) {
        setRove(id);
      },
      key(id, e) {
        const L = latest.current;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          const additive = e.shiftKey || e.metaKey || e.ctrlKey;
          if (!additive && e.key === "Enter" && (L.selected || []).includes(id)) L.onOpen && L.onOpen(id);
          else L.onSelect && L.onSelect(id, { additive });
          return;
        }
        if (e.key === "Escape") {
          if ((L.selected || []).length && L.onClear) {
            e.preventDefault();
            e.stopPropagation();
            L.onClear();
          }
          return;
        }
        const vec = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] }[e.key];
        if (!vec) return;
        e.preventDefault();
        const me = rf.getNode(id);
        if (!me) return;
        const linked = new Set();
        for (const ed of L.edges || []) {
          if (ed.source === id) linked.add(ed.target);
          else if (ed.target === id) linked.add(ed.source);
        }
        const pick = (cands) => {
          let best = null, bestScore = Infinity;
          for (const other of cands) {
            const o = rf.getNode(other);
            if (!o || other === id || !o.data?.n) continue;
            const dx = o.position.x - me.position.x, dy = o.position.y - me.position.y;
            const along = dx * vec[0] + dy * vec[1];
            if (along <= 0) continue;
            const score = Math.hypot(dx, dy) + Math.abs(dx * vec[1] + dy * vec[0]) * 1.5;
            if (score < bestScore) { best = other; bestScore = score; }
          }
          return best;
        };
        // a connected element in that direction first, else the nearest element there
        const best = pick(linked) || pick((L.nodes || []).map((n) => n.id));
        if (best) focusNode(best);
      },
    }),
    [rf, focusNode],
  );

  return (
    <Ctx.Provider value={ctx}>
      <div className="lg-root" ref={rootRef} data-layout={mode}>
        <svg className="lg-defs" aria-hidden="true" focusable="false">
          <defs>
            {[["lg-arrow", "lg-m"], ["lg-arrow-accent", "lg-m lg-m-accent"], ["lg-arrow-bad", "lg-m lg-m-bad"]].map(([id, c]) => (
              <marker key={id} id={id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse" markerUnits="userSpaceOnUse">
                <path className={c} d="M1 1.5 9 5 1 8.5Z" />
              </marker>
            ))}
          </defs>
        </svg>
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          nodeTypes={NODE_TYPES}
          onNodesChange={onNodesChange}
          onInit={() => { inited.current = true; setTimeout(() => latest.current.handle?.readable?.(), 0); }}
          onNodeDragStart={() => { dragged.current = false; }}
          onNodeDrag={() => { dragged.current = true; }}
          onNodeDragStop={() => { setTimeout(() => { dragged.current = false; }, 0); }}
          onPaneClick={() => { const L = latest.current; if ((L.selected || []).length && L.onClear) L.onClear(); }}
          nodesConnectable={false}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          deleteKeyCode={null}
          selectionKeyCode={null}
          multiSelectionKeyCode={null}
          panOnDrag
          panOnScroll
          zoomOnScroll={false}
          zoomOnPinch
          zoomOnDoubleClick={false}
          minZoom={0.05}
          maxZoom={2}
          proOptions={{ hideAttribution: false }}
          attributionPosition="top-right"
          aria-label="Business-logic graph"
        >
          <Panel position="bottom-left" className="lg-zoom">
            <button type="button" aria-label="Zoom in" title="Zoom in" onClick={() => rf.zoomIn({ duration: still() ? 0 : 150 })}><Icon name="plus" /></button>
            <button type="button" aria-label="Zoom out" title="Zoom out" onClick={() => rf.zoomOut({ duration: still() ? 0 : 150 })}><Icon name="minus" /></button>
            <button type="button" aria-label="Fit all" title="Fit all (small text)" onClick={fit}><Icon name="fit" /></button>
          </Panel>
          {minimap && <MiniMap pannable zoomable nodeClassName={(n) => "lg-mini-node" + (selSet.has(n.id) ? " is-selected" : "")} ariaLabel="Graph overview" />}
        </ReactFlow>
      </div>
    </Ctx.Provider>
  );
}

export function GraphApp(props) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
