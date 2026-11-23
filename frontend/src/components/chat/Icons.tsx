/** Small inline SVG icons (no emoji, so they render the same everywhere and inherit the text colour). */
const base = { width: 22, height: 22, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;

export const ChatIcon = () => (
  <svg {...base}><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z" /></svg>
);
export const CloseIcon = ({ size = 22 }: { size?: number }) => (
  <svg {...base} width={size} height={size}><path d="M6 6l12 12M18 6 6 18" /></svg>
);
