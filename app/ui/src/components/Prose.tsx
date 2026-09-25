import type { ReactNode } from "react";

/** Inline **bold** only; everything else stays literal text (no HTML is ever injected). */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : part,
  );
}

/** Answers and letters are plain paragraphs; outline packs use # headings and - bullets. Render both readably. */
export default function Prose({ text }: { text: string }) {
  const blocks = text.replace(/\r\n/g, "\n").split(/\n\s*\n/);
  return (
    <div className="space-y-4">
      {blocks.map((block, i) => {
        const lines = block.split("\n").filter((l) => l.trim());
        if (!lines.length) return null;
        const heading = lines[0].match(/^(#{1,3})\s+(.*)$/);
        if (heading && lines.length === 1) {
          const size = heading[1].length === 1 ? "text-[19px]" : "text-[17px]";
          return <h3 key={i} className={`${size} font-semibold leading-snug`}>{inline(heading[2])}</h3>;
        }
        if (lines.every((l) => /^\s*[-*]\s+/.test(l))) {
          return (
            <ul key={i} className="list-disc space-y-2 pl-5 marker:text-muted">
              {lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*[-*]\s+/, ""))}</li>)}
            </ul>
          );
        }
        return <p key={i} className="whitespace-pre-wrap">{inline(block)}</p>;
      })}
    </div>
  );
}
