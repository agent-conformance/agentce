/**
 * The report-file reader contract (SPEC 8.7): one written reader per format for every artifact that
 * `report --validate`, readiness, sign and `diff` read, the same in every engine, with no library as
 * the reference. Bytes are strict UTF-8 with no byte-order mark; CSV is RFC 4180; XML is well-formed
 * XML 1.0 with no DTD, no entity beyond the five predefined, nothing after the root element, no
 * character outside `Char` and every prefix bound; the empty check uses one whitespace set. JSON and
 * JSON Lines files keep the JSON profile reader (`json.ts`). Mirrors Python's `report_reader` and
 * Java's `ReportReader` (harness/remediation/evidence/P18-18.60/design.md). No network, no learned
 * component.
 */

import { JsonProfileError, decodeProfile } from "./json";

/** A file outside the reader contract: `rule` is a token of `spec/report/reader-cases.json`, `line` is
 * 1 + the LF characters before the fault. The reader stops at the first fault. */
export class ReaderError extends Error {
  constructor(
    readonly rule: string,
    readonly line: number,
  ) {
    super(`outside the report-file reader contract: ${rule} at line ${line} (SPEC 8.7)`);
    this.name = "ReaderError";
  }
}

/** `data` as text: a leading byte-order mark is `bom`, then bytes that are not strict UTF-8 (an
 * overlong form, an encoded surrogate, a code point past U+10FFFF, a truncated sequence) are
 * `invalid_utf8` at the line of the first bad byte. */
export function decodeReportText(data: Buffer): string {
  try {
    return decodeProfile(data, "refuse");
  } catch (err) {
    if (err instanceof JsonProfileError) {
      throw new ReaderError(err.rule, err.line);
    }
    throw err;
  }
}

/** Whether `text` holds only space, tab, CR and LF (none at all included): the empty check's one set. */
export function isBlank(text: string): boolean {
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c !== " " && c !== "\t" && c !== "\r" && c !== "\n") {
      return false;
    }
  }
  return true;
}

// --- CSV (RFC 4180) ---------------------------------------------------------------------------------

/** The records of `text` as RFC 4180 reads them: a record ends at LF or CR LF outside quotes, a quoted
 * field may hold line ends, commas and doubled quotes, and a final record end starts no record. An
 * empty line is a record with no fields, even at the very end (SPEC 8.7). Throws {@link ReaderError}
 * for a bare CR outside quotes, text after a closing quote, a quote inside an unquoted field, or end of
 * text inside a quoted field (at the line of its opening quote). */
export function readCsv(text: string): string[][] {
  const records: string[][] = [];
  const n = text.length;
  let i = 0;
  let line = 1;
  const recordEndAt = (at: number): boolean =>
    text[at] === "\n" || (text[at] === "\r" && text[at + 1] === "\n");
  while (i < n) {
    const record: string[] = [];
    if (recordEndAt(i)) {
      i += text[i] === "\r" ? 2 : 1;
      line += 1;
      records.push(record);
      continue;
    }
    for (;;) {
      let field = "";
      if (text[i] === '"') {
        const opened = line;
        i += 1;
        for (;;) {
          if (i >= n) {
            throw new ReaderError("csv_unclosed_quote", opened);
          }
          const c = text[i];
          if (c === '"') {
            if (text[i + 1] === '"') {
              field += '"';
              i += 2;
              continue;
            }
            i += 1;
            break;
          }
          if (c === "\n") {
            line += 1;
          }
          field += c;
          i += 1;
        }
        if (i < n && text[i] !== "," && !recordEndAt(i)) {
          throw new ReaderError("csv_text_after_quote", line);
        }
      } else {
        while (i < n && text[i] !== "," && text[i] !== "\n") {
          const c = text[i];
          if (c === '"') {
            throw new ReaderError("csv_quote_in_field", line);
          }
          if (c === "\r") {
            if (text[i + 1] === "\n") {
              break;
            }
            throw new ReaderError("csv_bare_cr", line);
          }
          field += c;
          i += 1;
        }
      }
      record.push(field);
      if (i >= n) {
        break;
      }
      if (text[i] === ",") {
        i += 1;
        continue;
      }
      i += text[i] === "\r" ? 2 : 1;
      line += 1;
      break;
    }
    records.push(record);
  }
  return records;
}

// --- XML 1.0 well-formedness ------------------------------------------------------------------------

const PREDEFINED_ENTITIES: ReadonlySet<string> = new Set(["amp", "lt", "gt", "quot", "apos"]);

/** XML 1.0 `Char`: #x9, #xA, #xD, #x20-#xD7FF, #xE000-#xFFFD, #x10000-#x10FFFF. */
function isXmlChar(cp: number): boolean {
  return (
    cp === 0x9 ||
    cp === 0xa ||
    cp === 0xd ||
    (cp >= 0x20 && cp <= 0xd7ff) ||
    (cp >= 0xe000 && cp <= 0xfffd) ||
    (cp >= 0x10000 && cp <= 0x10ffff)
  );
}

/** XML 1.0 Fifth Edition `NameStartChar`. */
function isNameStartChar(cp: number): boolean {
  return (
    cp === 0x3a ||
    (cp >= 0x41 && cp <= 0x5a) ||
    cp === 0x5f ||
    (cp >= 0x61 && cp <= 0x7a) ||
    (cp >= 0xc0 && cp <= 0xd6) ||
    (cp >= 0xd8 && cp <= 0xf6) ||
    (cp >= 0xf8 && cp <= 0x2ff) ||
    (cp >= 0x370 && cp <= 0x37d) ||
    (cp >= 0x37f && cp <= 0x1fff) ||
    (cp >= 0x200c && cp <= 0x200d) ||
    (cp >= 0x2070 && cp <= 0x218f) ||
    (cp >= 0x2c00 && cp <= 0x2fef) ||
    (cp >= 0x3001 && cp <= 0xd7ff) ||
    (cp >= 0xf900 && cp <= 0xfdcf) ||
    (cp >= 0xfdf0 && cp <= 0xfffd) ||
    (cp >= 0x10000 && cp <= 0xeffff)
  );
}

/** XML 1.0 Fifth Edition `NameChar`. */
function isNameChar(cp: number): boolean {
  return (
    isNameStartChar(cp) ||
    cp === 0x2d ||
    cp === 0x2e ||
    (cp >= 0x30 && cp <= 0x39) ||
    cp === 0xb7 ||
    (cp >= 0x300 && cp <= 0x36f) ||
    (cp >= 0x203f && cp <= 0x2040)
  );
}

const isXmlSpace = (c: string | undefined): boolean =>
  c === " " || c === "\t" || c === "\r" || c === "\n";

/** A well-formedness fault at a character index; turned into a {@link ReaderError} with its line. */
class XmlFault {
  constructor(
    readonly rule: string,
    readonly at: number,
  ) {}
}

interface OpenElement {
  readonly name: string;
  readonly prefixes: ReadonlySet<string>;
}

/** The hand-written scanner {@link checkXml} runs: prolog, one root element, then Misc. */
class XmlScanner {
  private pos = 0;
  private readonly stack: OpenElement[] = [];

  constructor(private readonly text: string) {}

  private fail(rule: string, at = this.pos): never {
    throw new XmlFault(rule, at);
  }

  private startsWith(s: string): boolean {
    return this.text.startsWith(s, this.pos);
  }

  private skipSpace(): boolean {
    const start = this.pos;
    while (isXmlSpace(this.text[this.pos])) {
      this.pos += 1;
    }
    return this.pos > start;
  }

  /** An XML `Name` at the cursor, or a syntax fault. */
  private name(): string {
    const start = this.pos;
    const first = this.text.codePointAt(this.pos);
    if (first === undefined || !isNameStartChar(first)) {
      this.fail("xml_syntax");
    }
    this.pos += first > 0xffff ? 2 : 1;
    for (;;) {
      const cp = this.text.codePointAt(this.pos);
      if (cp === undefined || !isNameChar(cp)) {
        break;
      }
      this.pos += cp > 0xffff ? 2 : 1;
    }
    return this.text.slice(start, this.pos);
  }

  /** The prefix of a QName (`""` for none); a name with two colons or an empty side is a syntax fault. */
  private prefixOf(qname: string, at: number): string {
    const parts = qname.split(":");
    if (parts.length > 2 || parts.some((p) => p === "")) {
      this.fail("xml_syntax", at);
    }
    return parts.length === 2 ? (parts[0] as string) : "";
  }

  private bound(prefix: string, own: ReadonlySet<string>): boolean {
    if (prefix === "" || prefix === "xml" || own.has(prefix)) {
      return true;
    }
    return this.stack.some((element) => element.prefixes.has(prefix));
  }

  scan(): void {
    const text = this.text;
    if (text.startsWith("<?xml") && (isXmlSpace(text[5]) || text[5] === "?")) {
      this.xmlDeclaration();
    }
    this.misc();
    if (this.pos >= text.length || text[this.pos] !== "<") {
      this.fail("xml_syntax");
    }
    this.element();
    this.misc();
    if (this.pos < text.length) {
      this.fail(this.startsWith("<!DOCTYPE") ? "xml_dtd" : "xml_after_root");
    }
  }

  /** Whitespace, comments and processing instructions; a `<!DOCTYPE` here is `xml_dtd`. */
  private misc(): void {
    for (;;) {
      this.skipSpace();
      if (this.startsWith("<!--")) {
        this.comment();
      } else if (this.startsWith("<?")) {
        this.processingInstruction();
      } else if (this.startsWith("<!DOCTYPE")) {
        this.fail("xml_dtd");
      } else {
        return;
      }
    }
  }

  /** `<?xml` S version [S encoding] [S standalone] S? `?>` at offset 0; the encoding is UTF-8. */
  private xmlDeclaration(): void {
    this.pos = 5;
    const pseudo = (name: string, value: RegExp, required: boolean): string | undefined => {
      const save = this.pos;
      if (!this.skipSpace() || !this.startsWith(name)) {
        if (required) {
          this.fail("xml_syntax");
        }
        this.pos = save;
        return undefined;
      }
      this.pos += name.length;
      this.skipSpace();
      if (this.text[this.pos] !== "=") {
        this.fail("xml_syntax");
      }
      this.pos += 1;
      this.skipSpace();
      const quote = this.text[this.pos];
      if (quote !== '"' && quote !== "'") {
        this.fail("xml_syntax");
      }
      const end = this.text.indexOf(quote, this.pos + 1);
      const literal = end < 0 ? "" : this.text.slice(this.pos + 1, end);
      if (end < 0 || !value.test(literal)) {
        this.fail("xml_syntax");
      }
      this.pos = end + 1;
      return literal;
    };
    pseudo("version", /^1\.[0-9]+$/, true);
    const encoding = pseudo("encoding", /^[A-Za-z][A-Za-z0-9._-]*$/, false);
    if (encoding !== undefined && encoding.toLowerCase() !== "utf-8") {
      this.fail("xml_syntax");
    }
    pseudo("standalone", /^(yes|no)$/, false);
    this.skipSpace();
    if (!this.startsWith("?>")) {
      this.fail("xml_syntax");
    }
    this.pos += 2;
  }

  /** `<!--` ... `-->` with no `--` inside and not ending in `-`. */
  private comment(): void {
    const start = this.pos;
    const end = this.text.indexOf("-->", start + 4);
    if (end < 0) {
      this.fail("xml_syntax", start);
    }
    const body = this.text.slice(start + 4, end);
    if (body.includes("--") || body.endsWith("-")) {
      this.fail("xml_syntax", start);
    }
    this.pos = end + 3;
  }

  /** `<?target ...?>`; a target `xml` in any case is the declaration out of place. */
  private processingInstruction(): void {
    const start = this.pos;
    this.pos += 2;
    const target = this.name();
    if (target.toLowerCase() === "xml" || target.includes(":")) {
      this.fail("xml_syntax", start);
    }
    if (this.startsWith("?>")) {
      this.pos += 2;
      return;
    }
    if (!this.skipSpace()) {
      this.fail("xml_syntax");
    }
    const end = this.text.indexOf("?>", this.pos);
    if (end < 0) {
      this.fail("xml_syntax", start);
    }
    this.pos = end + 2;
  }

  /** A reference at `&`: one of the five predefined entities or a character reference to a `Char`. */
  private reference(): void {
    const start = this.pos;
    const text = this.text;
    if (text[start + 1] === "#") {
      const hex = text[start + 2] === "x";
      const match = (hex ? /^[0-9a-fA-F]+;/ : /^[0-9]+;/).exec(
        text.slice(start + (hex ? 3 : 2), start + (hex ? 3 : 2) + 16),
      );
      if (match === null) {
        this.fail("xml_syntax", start);
      }
      const digits = match[0].slice(0, -1);
      const cp =
        digits.length > 8 ? Number.POSITIVE_INFINITY : Number.parseInt(digits, hex ? 16 : 10);
      if (!isXmlChar(cp)) {
        this.fail("xml_control_char", start);
      }
      this.pos = start + (hex ? 3 : 2) + match[0].length;
      return;
    }
    this.pos = start + 1;
    const first = text.codePointAt(this.pos);
    if (first === undefined || !isNameStartChar(first)) {
      this.fail("xml_syntax", start);
    }
    const name = this.name();
    if (text[this.pos] !== ";") {
      this.fail("xml_syntax", start);
    }
    if (!PREDEFINED_ENTITIES.has(name)) {
      this.fail("xml_entity", start);
    }
    this.pos += 1;
  }

  /** A start tag at `<`; returns whether it was an empty-element tag. */
  private startTag(): boolean {
    const start = this.pos;
    this.pos += 1;
    const name = this.name();
    const attributes: Array<[string, number]> = [];
    const seen = new Set<string>();
    const prefixes = new Set<string>();
    let empty: boolean;
    for (;;) {
      const spaced = this.skipSpace();
      if (this.startsWith("/>")) {
        this.pos += 2;
        empty = true;
        break;
      }
      if (this.text[this.pos] === ">") {
        this.pos += 1;
        empty = false;
        break;
      }
      if (!spaced) {
        this.fail("xml_syntax");
      }
      const at = this.pos;
      const attribute = this.name();
      if (seen.has(attribute)) {
        this.fail("xml_syntax", at);
      }
      seen.add(attribute);
      this.skipSpace();
      if (this.text[this.pos] !== "=") {
        this.fail("xml_syntax");
      }
      this.pos += 1;
      this.skipSpace();
      const quote = this.text[this.pos];
      if (quote !== '"' && quote !== "'") {
        this.fail("xml_syntax");
      }
      this.pos += 1;
      const valueStart = this.pos;
      for (;;) {
        const c = this.text[this.pos];
        if (c === undefined || c === "<") {
          this.fail("xml_syntax");
        }
        if (c === quote) {
          break;
        }
        if (c === "&") {
          this.reference();
        } else {
          this.pos += 1;
        }
      }
      const value = this.text.slice(valueStart, this.pos);
      this.pos += 1;
      const prefix = this.prefixOf(attribute, at);
      if (prefix === "xmlns") {
        if (value === "") {
          this.fail("xml_syntax", at);
        }
        prefixes.add(attribute.slice("xmlns:".length));
      }
      attributes.push([attribute, at]);
    }
    const prefix = this.prefixOf(name, start + 1);
    if (prefix === "xmlns") {
      this.fail("xml_syntax", start + 1);
    }
    if (!this.bound(prefix, prefixes)) {
      this.fail("xml_unbound_prefix", start + 1);
    }
    for (const [attribute, at] of attributes) {
      const attributePrefix = this.prefixOf(attribute, at);
      if (attributePrefix !== "xmlns" && !this.bound(attributePrefix, prefixes)) {
        this.fail("xml_unbound_prefix", at);
      }
    }
    if (!empty) {
      this.stack.push({ name, prefixes });
    }
    return empty;
  }

  /** The root element and everything inside it, without recursion. */
  private element(): void {
    if (this.startTag()) {
      return;
    }
    const text = this.text;
    while (this.stack.length > 0) {
      const c = text[this.pos];
      if (c === undefined) {
        this.fail("xml_syntax");
      }
      if (c === "<") {
        if (this.startsWith("</")) {
          const at = this.pos;
          this.pos += 2;
          const name = this.name();
          this.skipSpace();
          if (
            text[this.pos] !== ">" ||
            name !== (this.stack[this.stack.length - 1] as OpenElement).name
          ) {
            this.fail("xml_syntax", at);
          }
          this.pos += 1;
          this.stack.pop();
        } else if (this.startsWith("<!--")) {
          this.comment();
        } else if (this.startsWith("<![CDATA[")) {
          const end = text.indexOf("]]>", this.pos + 9);
          if (end < 0) {
            this.fail("xml_syntax");
          }
          this.pos = end + 3;
        } else if (this.startsWith("<!DOCTYPE")) {
          this.fail("xml_dtd");
        } else if (this.startsWith("<?")) {
          this.processingInstruction();
        } else if (this.startsWith("<!")) {
          this.fail("xml_syntax");
        } else {
          this.startTag();
        }
      } else if (c === "&") {
        this.reference();
      } else if (c === "]" && this.startsWith("]]>")) {
        this.fail("xml_syntax");
      } else {
        this.pos += 1;
      }
    }
  }
}

/** The index of the first character of `text` outside XML 1.0 `Char`, or -1. */
function firstNonXmlChar(text: string): number {
  for (let i = 0; i < text.length; i++) {
    const cp = text.codePointAt(i) as number;
    if (!isXmlChar(cp)) {
      return i;
    }
    if (cp > 0xffff) {
      i += 1;
    }
  }
  return -1;
}

function lineAt(text: string, index: number): number {
  let line = 1;
  for (let i = 0; i < index; i++) {
    if (text.charCodeAt(i) === 0x0a) {
      line += 1;
    }
  }
  return line;
}

/** Throw {@link ReaderError} unless `text` is a well-formed XML 1.0 document under the contract: the
 * first fault in text order names its token (`xml_dtd`, `xml_entity`, `xml_after_root`,
 * `xml_control_char`, `xml_unbound_prefix`, else `xml_syntax`). */
export function checkXml(text: string): void {
  const control = firstNonXmlChar(text);
  let fault: XmlFault | undefined;
  try {
    new XmlScanner(text).scan();
  } catch (exc) {
    if (!(exc instanceof XmlFault)) {
      throw exc;
    }
    fault = exc;
  }
  if (control >= 0 && (fault === undefined || control <= fault.at)) {
    fault = new XmlFault("xml_control_char", control);
  }
  if (fault !== undefined) {
    throw new ReaderError(fault.rule, lineAt(text, fault.at));
  }
}
