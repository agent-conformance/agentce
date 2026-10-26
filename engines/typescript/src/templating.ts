/**
 * A minimal, logic-less Mustache-style renderer (SPEC §7: one language-neutral template in `spec/`).
 *
 * A faithful port of the Python reference (`templating.py`): `{{path}}` interpolation (dotted paths
 * resolved against a scope stack, innermost first), `{{#path}}...{{/path}}` sections (iterate a list,
 * pushing each item; push a non-empty mapping; render once in the same scope for any other truthy
 * value), `{{^path}}...{{/path}}` inverted sections (render only for an empty or falsy value), and
 * `{{.}}` for the current scope. Truthiness and the text of a value are Python's, so every engine
 * renders the same template to the same bytes. The template supplies every word that is not a
 * substitution; nothing in the context is evaluated (SPEC §7 injection hardening).
 */

import { pyStr } from "./util";

const TAG_RE = /\{\{([#^/]?)([a-zA-Z0-9_.]+|\.)\}\}/g;

type Node =
  | { kind: "text"; value: string }
  | { kind: "var"; name: string }
  | { kind: "section" | "inverted"; name: string; children: Node[] };

function isMapping(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Python's truthiness for the JSON kinds: null, false, 0, "", [] and {} are falsy. */
export function pyTruthy(value: unknown): boolean {
  if (value === null || value === undefined || value === false || value === 0 || value === "") {
    return false;
  }
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (isMapping(value)) {
    return Object.keys(value).length > 0;
  }
  return true;
}

function parse(template: string): Node[] {
  const root: Node[] = [];
  const stack: Node[][] = [root];
  const openNames: string[] = [];
  let pos = 0;
  for (const m of template.matchAll(TAG_RE)) {
    const start = m.index ?? 0;
    const top = stack[stack.length - 1] as Node[];
    if (start > pos) {
      top.push({ kind: "text", value: template.slice(pos, start) });
    }
    const sigil = m[1] as string;
    const name = m[2] as string;
    if (sigil === "#" || sigil === "^") {
      const children: Node[] = [];
      top.push({ kind: sigil === "#" ? "section" : "inverted", name, children });
      stack.push(children);
      openNames.push(name);
    } else if (sigil === "/") {
      if (openNames.length === 0 || openNames[openNames.length - 1] !== name) {
        throw new Error(`mismatched closing tag {/${name}} in template`);
      }
      openNames.pop();
      stack.pop();
    } else {
      top.push({ kind: "var", name });
    }
    pos = start + m[0].length;
  }
  if (pos < template.length) {
    (stack[stack.length - 1] as Node[]).push({ kind: "text", value: template.slice(pos) });
  }
  if (openNames.length > 0) {
    throw new Error(`unclosed section tag(s) in template: ${pyStr(openNames)}`);
  }
  return root;
}

function lookup(scopes: unknown[], path: string): unknown {
  if (path === ".") {
    return scopes[scopes.length - 1];
  }
  const [head, ...rest] = path.split(".") as [string, ...string[]];
  for (let i = scopes.length - 1; i >= 0; i--) {
    const scope = scopes[i];
    if (isMapping(scope) && Object.hasOwn(scope, head)) {
      let value: unknown = scope[head];
      for (const part of rest) {
        value = isMapping(value) ? value[part] : null;
      }
      return value ?? null;
    }
  }
  return null;
}

function renderNodes(nodes: Node[], scopes: unknown[]): string {
  let out = "";
  for (const node of nodes) {
    if (node.kind === "text") {
      out += node.value;
    } else if (node.kind === "var") {
      const value = lookup(scopes, node.name);
      out += value === null ? "" : pyStr(value);
    } else if (node.kind === "section") {
      const value = lookup(scopes, node.name);
      if (Array.isArray(value)) {
        for (const item of value) {
          out += renderNodes(node.children, [...scopes, item]);
        }
      } else if (isMapping(value)) {
        if (Object.keys(value).length > 0) {
          out += renderNodes(node.children, [...scopes, value]);
        }
      } else if (pyTruthy(value)) {
        out += renderNodes(node.children, scopes);
      }
    } else if (!pyTruthy(lookup(scopes, node.name))) {
      out += renderNodes(node.children, scopes);
    }
  }
  return out;
}

/** Render `template` against `context`: a pure function of its two arguments, so the same context
 * always renders the same bytes. A mismatched or unclosed section tag throws. */
export function render(template: string, context: Record<string, unknown>): string {
  return renderNodes(parse(template), [context]);
}
