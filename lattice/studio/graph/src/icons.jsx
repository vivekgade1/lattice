// 16px inline icons, same path set as web/js/util.js. Always paired with a word.
const PATHS = {
  check: "M6.2 11.6 2.6 8l1.1-1.1 2.5 2.5 6.1-6.1L13.4 4.4l-7.2 7.2Z",
  cross: "M4.1 3 8 6.9 11.9 3 13 4.1 9.1 8l3.9 3.9-1.1 1.1L8 9.1 4.1 13 3 11.9 6.9 8 3 4.1 4.1 3Z",
  dash: "M3 7.25h10v1.5H3z",
  info: "M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm.75 11h-1.5V7h1.5v5Zm0-6.5h-1.5V4h1.5v1.5Z",
  question:
    "M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm.1 11.2a.9.9 0 1 1 0-1.8.9.9 0 0 1 0 1.8Zm1.3-4.3c-.6.4-.7.6-.7 1.1v.3H7.3V9c0-1 .4-1.5 1.1-2 .5-.4.7-.6.7-1 0-.5-.4-.8-1-.8s-1.1.4-1.1 1H5.5C5.6 4.8 6.6 4 8.1 4c1.6 0 2.5.8 2.5 2 0 .9-.5 1.4-1.2 1.9Z",
  triangle: "M8 1.5 15 14H1L8 1.5Zm-.75 4.5v4h1.5V6h-1.5Zm0 5v1.5h1.5V11h-1.5Z",
  dot: "M8 4.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7Z",
  plus: "M7.25 3h1.5v4.25H13v1.5H8.75V13h-1.5V8.75H3v-1.5h4.25z",
  minus: "M3 7.25h10v1.5H3z",
  fit: "M2 2h4v1.5H3.5V6H2V2Zm8 0h4v4h-1.5V3.5H10V2ZM2 10h1.5v2.5H6V14H2v-4Zm10.5 0H14v4h-4v-1.5h2.5V10Z",
  diamond: "M8 1.5 14.5 8 8 14.5 1.5 8 8 1.5Z",
  "shield-check": "M8 1.5 2.5 3.5v4c0 3.3 2.3 5.9 5.5 7 3.2-1.1 5.5-3.7 5.5-7v-4L8 1.5Zm-1 9.2L4.6 8.3l1-1L7 8.7l3.4-3.4 1 1L7 10.7Z",
  "text-lines": "M2 3h12v1.5H2zM2 6.5h12V8H2zM2 10h8v1.5H2z",
  // kind glyphs
  rule: "M3 2h10v12H3V2Zm1.5 2v1.5h7V4h-7Zm0 3v1.5h7V7h-7Zm0 3v1.5h4V10h-4Z",
  endpoint: "M1 7.25h5.5v1.5H1zM9.5 7.25H15v1.5H9.5zM8 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8Z",
  table: "M2 3h12v10H2V3Zm1.5 1.5V6h9V4.5h-9Zm0 3V9h2.5V7.5H3.5Zm4 0V9h5V7.5h-5Zm-4 3V12H6v-1.5H3.5Zm4 0V12h5v-1.5h-5Z",
  service: "M2 2h12v5H2V2Zm0 7h12v5H2V9Zm2-5v1.5h1.5V4H4Zm0 7v1.5h1.5V11H4Z",
  scenario: "M4 2.5 13 8l-9 5.5v-11Z",
  code: "M5.5 4 1.5 8l4 4 1-1L3.6 8l2.9-3-1-1Zm5 0-1 1L12.4 8l-2.9 3 1 1 4-4-4-4Z",
  state: "M8 2a6 6 0 1 1 0 12A6 6 0 0 1 8 2Zm0 2a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z",
  stage: "M2 3h7l5 5-5 5H2l5-5-5-5Z",
  machine: "M8 2a6 6 0 1 1-5.6 8.2l1.4-.5A4.5 4.5 0 1 0 3.5 8H5L2.5 11 0 8h2A6 6 0 0 1 8 2Z",
  invariant: "M3 3h10v1.5H3zM3 7.25h10v1.5H3zM3 11.5h10V13H3z",
  // build plan nodes: tier, status
  bolt: "M9.5 1 3 9h4l-1 6 6.5-8.5H8.5L9.5 1Z",
  bars: "M2 9.5h3V14H2V9.5Zm4.5-4h3V14h-3V5.5ZM11 2h3v12h-3V2Z",
  star: "M8 1l2.1 4.6 5 .6-3.7 3.4 1 5L8 12.1 3.6 14.6l1-5L.9 6.2l5-.6L8 1Z",
  clock: "M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm.75 3v3.7l2.6 1.5-.75 1.3L7.25 8.6V4h1.5Z",
  lock: "M5 6V4.5a3 3 0 0 1 6 0V6h1v8H4V6h1Zm1.5 0h3V4.5a1.5 1.5 0 0 0-3 0V6Z",
  plan: "M2 3h5v3H2V3Zm7 0h5v3H9V3ZM2 10h5v3H2v-3Zm7 0h5v3H9v-3ZM7 4.25h2v1.5H7zM7 11.25h2v1.5H7z",
  caller: "M3 2h4v1.5H4.5v9H7V14H3V2Zm5 5.25h5.2L11 5l1-1 4 4-4 4-1-1 2.2-2.25H8v-1.5Z",
};

export function Icon({ name, size = 14, className }) {
  const d = PATHS[name];
  if (!d) return null;
  return (
    <svg className={"lg-icon" + (className ? " " + className : "")} width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d={d} fill="currentColor" />
    </svg>
  );
}
