// Simplified brand glyphs, drawn at 24x24 with currentColor so each can be tinted.
const glyphs = {
  x: (
    <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
  ),
  bluesky: (
    <path d="M12 10.8c-1.087-2.114-4.046-6.053-6.798-7.995C2.566.944 1.561 1.266.902 1.565.139 1.908 0 3.08 0 3.768c0 .69.378 5.65.624 6.479.815 2.736 3.713 3.66 6.383 3.364.136-.02.275-.039.415-.056-.138.022-.276.04-.415.056-3.912.58-7.387 2.005-2.83 7.078 5.013 5.19 6.87-1.113 7.823-4.308.953 3.195 2.05 9.271 7.733 4.308 4.267-4.308 1.172-6.498-2.74-7.078a8.741 8.741 0 0 1-.415-.056c.14.017.279.036.415.056 2.67.297 5.568-.628 6.383-3.364.246-.828.624-5.79.624-6.478 0-.69-.139-1.861-.902-2.206-.659-.298-1.664-.62-4.3 1.24C16.046 4.748 13.087 8.687 12 10.8Z" />
  ),
  reddit: (
    <>
      <ellipse cx="12" cy="14.4" rx="8" ry="5.6" />
      <circle cx="4.6" cy="11.4" r="2.1" />
      <circle cx="19.4" cy="11.4" r="2.1" />
      <circle cx="17.6" cy="4.6" r="1.7" />
      <path d="M12.2 8.9 13.1 3.6l4.1 1" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="9" cy="13.6" r="1.35" fill="var(--glyph-cut, #fff)" />
      <circle cx="15" cy="13.6" r="1.35" fill="var(--glyph-cut, #fff)" />
      <path d="M9.3 16.9c1.6 1.1 3.8 1.1 5.4 0" fill="none" stroke="var(--glyph-cut, #fff)" strokeWidth="1.3" strokeLinecap="round" />
    </>
  ),
  facebook: (
    <path d="M13.6 22v-8.2h2.8l.5-3.3h-3.3V8.4c0-.95.4-1.7 1.8-1.7h1.6V3.8c-.3 0-1.3-.15-2.5-.15-2.6 0-4.3 1.6-4.3 4.4v2.5H7.3v3.3h2.9V22z" />
  ),
  instagram: (
    <>
      <rect x="3.2" y="3.2" width="17.6" height="17.6" rx="5" fill="none" stroke="currentColor" strokeWidth="1.9" />
      <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" strokeWidth="1.9" />
      <circle cx="17.3" cy="6.7" r="1.2" />
    </>
  ),
  linkedin: (
    <>
      <rect x="3.5" y="9" width="3.8" height="11.5" rx=".4" />
      <circle cx="5.4" cy="5.3" r="2.2" />
      <path d="M10 9h3.6v1.7c.6-1.1 1.9-2 3.8-2 3.5 0 4.1 2.3 4.1 5.2v6.6h-3.8v-5.8c0-1.4 0-3.1-1.9-3.1s-2.1 1.5-2.1 3v5.9H10z" />
    </>
  ),
};

export type PlatformId = keyof typeof glyphs;

export default function PlatformIcon({ name, size = 28 }: { name: PlatformId; size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      {glyphs[name]}
    </svg>
  );
}

// Platforms with a compose link (see mcp_tools/share_links.py). Instagram has none yet.
export const PLATFORMS: { id: PlatformId; name: string; note: string; color: string }[] = [
  { id: "x", name: "X", note: "Posts and replies", color: "#000000" },
  { id: "bluesky", name: "Bluesky", note: "Posts and replies", color: "#1185fe" },
  { id: "reddit", name: "Reddit", note: "Posts", color: "#ff4500" },
  { id: "facebook", name: "Facebook", note: "Link shares", color: "#1877f2" },
  { id: "linkedin", name: "LinkedIn", note: "Link shares", color: "#0a66c2" },
];
