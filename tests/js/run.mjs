// Runs one of the page scripts under jsdom, the way Selenium's execute_script
// runs it, and prints what it returned as JSON.
//
//   node run.mjs <scripts> <html> <args-json> [setup-js]
//
// <scripts>   file names under js/, joined with "+" and concatenated in order,
//             exactly as core/js.py's script() does (e.g. helpers.js+usable.js).
//             "-" means the source is in the SCRIPT_SOURCE environment variable,
//             for the one script that Python composes as a string rather than
//             keeping in js/ (site_data.fill).
// <html>      the document the script runs against.
// <args-json> a JSON array, bound as `arguments`. An entry {"$css": "<sel>"}
//             becomes the first element matching it, as a WebElement argument
//             does in a real session.
// [setup-js]  optional, evaluated in the window before the script. jsdom does no
//             layout, so a test that needs geometry or elementFromPoint stubs it
//             here rather than pretending the engine has it.
//
// The script is evaluated as a function body, which is what WebDriver does and
// why every script has a top-level `return`. The return is printed as JSON; an
// element anywhere in it is serialised as a descriptor, tag + #id + .class
// (e.g. "button#save.primary"), because a node has no JSON form and the
// descriptor is what a test can state. undefined prints as null. A script that
// throws exits 1 with the message on stderr.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const { JSDOM } = createRequire(join(root, "ui", "package.json"))("jsdom");

const [scripts, html, argsJson, setup] = process.argv.slice(2);

const source = scripts === "-"
  ? process.env.SCRIPT_SOURCE
  : scripts.split("+")
    .map((name) => readFileSync(join(root, "js", name), "utf8"))
    .join("\n");

const dom = new JSDOM(html, { pretendToBeVisual: true, runScripts: "outside-only", url: "https://example.test/" });
const { window } = dom;
if (setup) window.eval(setup);

const args = JSON.parse(argsJson).map((arg) => {
  if (arg && typeof arg === "object" && "$css" in arg) {
    const found = window.document.querySelector(arg.$css);
    if (!found) throw new Error("no element matches " + arg.$css);
    return found;
  }
  return arg;
});

const describe = (el) => {
  const id = el.id ? "#" + el.id : "";
  const first = (el.getAttribute("class") || "").trim().split(/\s+/)[0];
  return el.tagName.toLowerCase() + id + (first ? "." + first : "");
};

const plain = (value) => {
  if (value === undefined) return null;
  if (value === null || typeof value !== "object") return value;
  if (value.nodeType === 1) return describe(value);
  if (Array.isArray(value)) return value.map(plain);
  return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, plain(v)]));
};

try {
  const body = window.eval("(function () {\n" + source + "\n})");
  process.stdout.write(JSON.stringify(plain(body.apply(null, args))));
} catch (error) {
  process.stderr.write(String(error && error.stack ? error.stack : error));
  process.exit(1);
}
