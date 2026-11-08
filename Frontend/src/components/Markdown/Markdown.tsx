// Small, safe Markdown renderer for agent output (reports, run summaries).
//
// Agent text can quote untrusted web content, so this never injects HTML: it
// builds React elements, and links are kept only for http(s) and mailto URLs.
// Supports headings, paragraphs, nested lists, GFM tables, blockquotes, code
// fences, rules, and inline bold / italic / strike / code / links / bare URLs.
import { Fragment, type ReactNode } from "react";
import "./Markdown.css";

type Block =
  | { t: "h"; level: number; text: string }
  | { t: "p"; text: string }
  | { t: "code"; text: string }
  | { t: "hr" }
  | { t: "quote"; blocks: Block[] }
  | { t: "list"; ordered: boolean; start: number; items: Block[][] }
  | { t: "table"; head: string[]; align: (string | undefined)[]; rows: string[][] };

const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const FENCE = /^ {0,3}(```|~~~)/;
const HR = /^ {0,3}([-*_])(\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}>\s?/;
const LIST_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

const indentOf = (line: string) => line.match(/^\s*/)![0].length;
const isBlank = (line: string) => line.trim() === "";

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

const isTableStart = (lines: string[], i: number) =>
  lines[i].includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-");

function startsBlock(lines: string[], i: number): boolean {
  const line = lines[i];
  return (
    HEADING.test(line) || FENCE.test(line) || HR.test(line) || QUOTE.test(line) || LIST_ITEM.test(line) ||
    isTableStart(lines, i)
  );
}

function parseList(lines: string[], i: number): [Block, number] {
  const first = lines[i].match(LIST_ITEM)!;
  const base = first[1].length;
  const ordered = /\d/.test(first[2]);
  const start = ordered ? parseInt(first[2], 10) : 1;
  const items: string[][] = [];
  let contentIndent = 0;
  let prevBlank = false;

  for (; i < lines.length; i++) {
    const line = lines[i];
    if (isBlank(line)) {
      prevBlank = true;
      if (items.length) items[items.length - 1].push("");
      continue;
    }
    const m = line.match(LIST_ITEM);
    const indent = indentOf(line);
    if (m && indent <= base + 1 && /\d/.test(m[2]) === ordered) {
      items.push([m[3]]);
      contentIndent = m[1].length + m[2].length + 1;
      prevBlank = false;
      continue;
    }
    if (m && indent <= base + 1) break; // switched between ordered and bulleted: new list
    if (indent > base) {
      items[items.length - 1].push(line.slice(Math.min(indent, contentIndent)));
      prevBlank = false;
      continue;
    }
    // Unindented text right after an item continues it ("lazy" line); anything else ends the list.
    if (!prevBlank && !startsBlock(lines, i)) {
      items[items.length - 1].push(line.trim());
      continue;
    }
    break;
  }
  for (const body of items) while (body.length > 1 && body[body.length - 1] === "") body.pop();
  return [{ t: "list", ordered, start, items: items.map((body) => parseBlocks(body)) }, i];
}

function parseBlocks(lines: string[]): Block[] {
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (isBlank(line)) {
      i++;
      continue;
    }
    const fence = line.match(FENCE);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      i++;
      blocks.push({ t: "code", text: body.join("\n") });
      continue;
    }
    const h = line.match(HEADING);
    if (h) {
      blocks.push({ t: "h", level: h[1].length, text: h[2] });
      i++;
      continue;
    }
    if (HR.test(line) && !LIST_ITEM.test(line)) {
      blocks.push({ t: "hr" });
      i++;
      continue;
    }
    if (isTableStart(lines, i)) {
      const head = splitRow(line);
      const align = splitRow(lines[i + 1]).map((c) =>
        c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : undefined,
      );
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && !isBlank(lines[i]) && lines[i].includes("|")) rows.push(splitRow(lines[i++]));
      blocks.push({ t: "table", head, align, rows });
      continue;
    }
    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && (QUOTE.test(lines[i]) || (!isBlank(lines[i]) && !startsBlock(lines, i)))) {
        body.push(lines[i++].replace(QUOTE, ""));
      }
      blocks.push({ t: "quote", blocks: parseBlocks(body) });
      continue;
    }
    if (LIST_ITEM.test(line)) {
      const [list, next] = parseList(lines, i);
      blocks.push(list);
      i = next;
      continue;
    }
    const para: string[] = [line.trim()];
    i++;
    while (i < lines.length && !isBlank(lines[i]) && !startsBlock(lines, i)) para.push(lines[i++].trim());
    blocks.push({ t: "p", text: para.join("\n") });
  }
  return blocks;
}

// ---------- inline ----------

export const safeHref = (url: string): string | null => (/^(https?:\/\/|mailto:)/i.test(url.trim()) ? url.trim() : null);

const hostOf = (url: string) => {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
};

const INLINE =
  /`([^`\n]+)`|\[([^\]\n]+)\]\(\s*<?((?:[^()\s>]|\([^()\s]*\))+)>?(?:\s+"[^"]*")?\s*\)|\*\*(?=\S)([\s\S]+?)\*\*|__(?=\S)([\s\S]+?)__|~~(?=\S)([\s\S]+?)~~|\*(?=[^\s*])([\s\S]+?)\*|(?<![\w])_(?=[^\s_])([\s\S]+?)_(?![\w])|(https?:\/\/[^\s<>"]+)/g;

function link(href: string, children: ReactNode, key: string) {
  const safe = safeHref(href);
  if (!safe) return <Fragment key={key}>{children}</Fragment>;
  return (
    <a
      key={key}
      href={safe}
      target="_blank"
      rel="noopener noreferrer nofollow"
      title={safe.startsWith("mailto:") ? safe : `Opens ${hostOf(safe)}`}
    >
      {children}
    </a>
  );
}

function inline(text: string, keyPrefix = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  const push = (s: string) => {
    // Keep the author's single line breaks: agents often write "Label: value" lines.
    s.split("\n").forEach((part, j) => {
      if (j > 0) out.push(<br key={`${keyPrefix}-br${n++}`} />);
      if (part) out.push(part.replace(/\\([\\`*_[\]()#+\-.!|~])/g, "$1"));
    });
  };
  const re = new RegExp(INLINE.source, "g");
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    push(text.slice(last, m.index));
    const key = `${keyPrefix}-${n++}`;
    if (m[1] !== undefined) out.push(<code key={key}>{m[1]}</code>);
    else if (m[2] !== undefined) out.push(link(m[3], inline(m[2], key), key));
    else if (m[4] !== undefined || m[5] !== undefined) out.push(<strong key={key}>{inline(m[4] ?? m[5], key)}</strong>);
    else if (m[6] !== undefined) out.push(<del key={key}>{inline(m[6], key)}</del>);
    else if (m[7] !== undefined || m[8] !== undefined) out.push(<em key={key}>{inline(m[7] ?? m[8], key)}</em>);
    else if (m[9] !== undefined) {
      // Leave trailing punctuation (and an unmatched ")") outside the link.
      let url = m[9];
      let tail = "";
      const count = (s: string, c: string) => s.split(c).length - 1;
      for (;;) {
        const last = url.slice(-1);
        const strip = /[.,;:!?'\]]/.test(last) || (last === ")" && count(url, ")") > count(url, "("));
        if (!strip) break;
        tail = last + tail;
        url = url.slice(0, -1);
      }
      out.push(link(url, url, key));
      if (tail) push(tail);
    }
    last = re.lastIndex;
  }
  push(text.slice(last));
  return out;
}

// ---------- render ----------

export const slugify = (text: string) =>
  text
    .toLowerCase()
    .replace(/[`*_~[\]()]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

function render(blocks: Block[], ctx: { idPrefix?: string; tight?: boolean }, key = "b"): ReactNode[] {
  return blocks.map((b, i) => {
    const k = `${key}${i}`;
    switch (b.t) {
      case "h": {
        // Shift down one level: the page owns <h1>, so a report's "#" becomes <h2>.
        const level = Math.min(b.level + 1, 6);
        const Tag = `h${level}` as "h2";
        const id = ctx.idPrefix ? `${ctx.idPrefix}-${slugify(b.text)}` : undefined;
        return (
          <Tag key={k} id={id}>
            {inline(b.text, k)}
          </Tag>
        );
      }
      case "p":
        return ctx.tight ? <Fragment key={k}>{inline(b.text, k)}</Fragment> : <p key={k}>{inline(b.text, k)}</p>;
      case "code":
        return (
          <pre key={k}>
            <code>{b.text}</code>
          </pre>
        );
      case "hr":
        return <hr key={k} />;
      case "quote":
        return <blockquote key={k}>{render(b.blocks, { idPrefix: ctx.idPrefix }, k)}</blockquote>;
      case "list": {
        const items = b.items.map((item, j) => (
          // A one-paragraph item renders without <p> so simple lists stay compact.
          <li key={j}>{render(item, { idPrefix: ctx.idPrefix, tight: item.length === 1 && item[0].t === "p" }, `${k}-${j}-`)}</li>
        ));
        return b.ordered ? (
          <ol key={k} start={b.start !== 1 ? b.start : undefined}>
            {items}
          </ol>
        ) : (
          <ul key={k}>{items}</ul>
        );
      }
      case "table":
        return (
          <div key={k} className="md-table-wrap">
            <table>
              <thead>
                <tr>
                  {b.head.map((c, j) => (
                    <th key={j} style={{ textAlign: b.align[j] as "left" | undefined }}>
                      {inline(c, `${k}h${j}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {b.rows.map((row, r) => (
                  <tr key={r}>
                    {b.head.map((_, j) => (
                      <td key={j} style={{ textAlign: b.align[j] as "left" | undefined }}>
                        {inline(row[j] ?? "", `${k}r${r}c${j}`)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
    }
  });
}

const toLines = (source: string) => source.replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n");

/** Headings in the text, for a table of contents. */
export function outline(source: string): { level: number; text: string; slug: string }[] {
  return parseBlocks(toLines(source))
    .filter((b): b is Extract<Block, { t: "h" }> => b.t === "h")
    .map((b) => ({ level: b.level, text: b.text.replace(/[`*_~]/g, ""), slug: slugify(b.text) }));
}

/** Every http(s) link in the text, in order, without duplicates. */
export function extractLinks(source: string): string[] {
  const seen = new Set<string>();
  const re = /\]\(\s*<?(https?:\/\/[^)\s>]+)|(https?:\/\/[^\s<>"()\]]+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source))) {
    const url = (m[1] ?? m[2]).replace(/[.,;:!?']+$/, "");
    seen.add(url);
  }
  return [...seen];
}

export default function Markdown({
  source,
  idPrefix,
  className = "",
}: {
  source: string;
  /** Prefix for heading ids, so headings can be linked from a table of contents. */
  idPrefix?: string;
  className?: string;
}) {
  return <div className={`md ${className}`.trim()}>{render(parseBlocks(toLines(source)), { idPrefix })}</div>;
}
