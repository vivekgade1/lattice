// Bundles src/index.jsx to ../web/vendor/graph.js (+ graph.css), ESM, minified.
// React Flow's base.css carries hard-coded default colours; they are stripped here so
// the shipped stylesheet takes every colour from the app's CSS custom properties.
import { build } from "esbuild";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, "../web/vendor");

const stripDefaults = {
  name: "strip-xyflow-colour-defaults",
  setup(b) {
    b.onLoad({ filter: /@xyflow[\\/]react[\\/]dist[\\/]base\.css$/ }, (args) => {
      let css = readFileSync(args.path, "utf8");
      css = css.replace(/^\s*--xy-[a-z-]+-default:[^;]*;\s*$/gm, "");
      css = css.replace(/#[0-9a-fA-F]{3,8}\b/g, "currentColor");
      return { contents: css, loader: "css" };
    });
  },
};

await build({
  entryPoints: { graph: resolve(here, "src/index.jsx") },
  outdir: out,
  bundle: true,
  format: "esm",
  minify: true,
  sourcemap: false,
  target: "es2022",
  jsx: "automatic",
  legalComments: "none",
  define: { "process.env.NODE_ENV": '"production"' },
  loader: { ".css": "css" },
  plugins: [stripDefaults],
  logLevel: "info",
});
