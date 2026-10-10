import { createRoot } from "react-dom/client";
import "@xyflow/react/dist/base.css";
import "./graph.css";
import { GraphApp } from "./GraphApp.jsx";

/**
 * mount(el, props) -> { update(props), fit(), readable(), viewport(), destroy() }
 * props: see the header of GraphApp.jsx for every shape.
 */
export function mount(el, props = {}) {
  const root = createRoot(el);
  const handle = { rf: null, fit: null, readable: null };
  let current = props;
  let alive = true;
  const render = () => {
    if (alive) root.render(<GraphApp {...current} handle={handle} />);
  };
  render();
  return {
    update(next) {
      current = { ...current, ...next };
      render();
    },
    fit() {
      if (handle.fit) handle.fit();
    },
    readable() {
      if (handle.readable) handle.readable();
    },
    viewport() {
      return handle.rf ? handle.rf.getViewport() : null;
    },
    destroy() {
      alive = false;
      root.unmount();
    },
  };
}

export default { mount };
