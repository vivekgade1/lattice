// Small DOM helpers shared by every view. No framework: h() builds elements.

const SVG_NS = "http://www.w3.org/2000/svg";

/** Build an element. attrs: class, text, html (trusted only), on{Event}, data-*, aria-*, etc. */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

/** Build an SVG element. */
export function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") el.textContent = v;
    else el.setAttribute(k, v);
  }
  append(el, children);
  return el;
}

function setAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = Array.isArray(v) ? v.filter(Boolean).join(" ") : v;
    else if (k === "text") el.textContent = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

/** Element.append that skips null/false and flattens arrays (native append prints "null"). */
export function add(el, ...children) {
  append(el, children);
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function esc(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Icons: tiny inline SVGs, 16px, currentColor. Every use also carries a text label.
const ICON_PATHS = {
  "shield-check": "M8 1.5 2.5 3.5v4c0 3.3 2.3 5.9 5.5 7 3.2-1.1 5.5-3.7 5.5-7v-4L8 1.5Zm-1 9.2L4.6 8.3l1-1L7 8.7l3.4-3.4 1 1L7 10.7Z",
  "text-lines": "M2 3h12v1.5H2zM2 6.5h12V8H2zM2 10h8v1.5H2z",
  triangle: "M8 1.5 15 14H1L8 1.5Zm-.75 4.5v4h1.5V6h-1.5Zm0 5v1.5h1.5V11h-1.5Z",
  question: "M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm.1 11.2a.9.9 0 1 1 0-1.8.9.9 0 0 1 0 1.8Zm1.3-4.3c-.6.4-.7.6-.7 1.1v.3H7.3V9c0-1 .4-1.5 1.1-2 .5-.4.7-.6.7-1 0-.5-.4-.8-1-.8s-1.1.4-1.1 1H5.5C5.6 4.8 6.6 4 8.1 4c1.6 0 2.5.8 2.5 2 0 .9-.5 1.4-1.2 1.9Z",
  info: "M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm.75 11h-1.5V7h1.5v5Zm0-6.5h-1.5V4h1.5v1.5Z",
  sparkle: "M8 1l1.6 4.4L14 7l-4.4 1.6L8 13l-1.6-4.4L2 7l4.4-1.6L8 1Z",
  check: "M6.2 11.6 2.6 8l1.1-1.1 2.5 2.5 6.1-6.1L13.4 4.4l-7.2 7.2Z",
  cross: "M4.1 3 8 6.9 11.9 3 13 4.1 9.1 8l3.9 3.9-1.1 1.1L8 9.1 4.1 13 3 11.9 6.9 8 3 4.1 4.1 3Z",
  dot: "M8 4.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7Z",
  diamond: "M8 1.5 14.5 8 8 14.5 1.5 8 8 1.5Z",
  dash: "M3 7.25h10v1.5H3z",
  lock: "M5 6V4.5a3 3 0 0 1 6 0V6h1v8H4V6h1Zm1.5 0h3V4.5a1.5 1.5 0 0 0-3 0V6Z",
  pending: "M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm.75 3v3.7l2.6 1.5-.75 1.3L7.25 8.6V4h1.5Z",
  chat: "M2 3h12v8H6l-3 3v-3H2V3Z",
  close: "M4.1 3 8 6.9 11.9 3 13 4.1 9.1 8l3.9 3.9-1.1 1.1L8 9.1 4.1 13 3 11.9 6.9 8 3 4.1 4.1 3Z",
  play: "M4 2.5v11l9-5.5-9-5.5Z",
  search: "M7 2a5 5 0 0 1 4 8l3 3-1 1-3-3a5 5 0 1 1-3-9Zm0 1.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z",
  plus: "M7.25 2h1.5v5.25H14v1.5H8.75V14h-1.5V8.75H2v-1.5h5.25V2Z",
  minus: "M2 7.25h12v1.5H2z",
  sun: "M8 4.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7ZM7.3 0h1.4v2.5H7.3zM7.3 13.5h1.4V16H7.3zM0 7.3h2.5v1.4H0zM13.5 7.3H16v1.4h-2.5z",
};

export function icon(name, label = null) {
  const svg = s("svg", {
    class: "icon",
    viewBox: "0 0 16 16",
    width: "16",
    height: "16",
    "aria-hidden": label ? null : "true",
    role: label ? "img" : null,
    "aria-label": label,
    focusable: "false",
  });
  svg.append(s("path", { d: ICON_PATHS[name] || ICON_PATHS.dot, fill: "currentColor" }));
  return svg;
}

export function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export function plural(n, one, many = one + "s") {
  return `${n} ${n === 1 ? one : many}`;
}

export function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export const reducedMotion = () =>
  window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
