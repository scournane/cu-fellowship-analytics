import "./fonts";

// Storybook style: white paper, one saturated green for progress and headings,
// blue for interactive accents, calm grays for body copy, flat sticker shapes
// with 2px borders and 12px corners. No gradients, no shadows.
export const tokens = {
  eagerGreen: "#58cc02",
  storybookGreen: "#d7ffb8",
  sparkBlue: "#1cb0f6",
  freshLeaf: "#a5ed6e",
  nightInk: "#000437",
  paper: "#ffffff",
  charcoal: "#4b4b4b",
  pencil: "#777777",
  faded: "#afafaf",
  hairline: "#e5e5e5",
  radius: 12,
  border: 2,
  display: "'Nunito', 'Nunito Sans', ui-rounded, system-ui, sans-serif",
  body: "'Nunito Sans', 'Nunito', system-ui, sans-serif",
} as const;

// Legacy names, remapped so every scene picks up the new look.
export const theme = {
  bg: tokens.paper,
  surface: tokens.paper,
  card: tokens.paper,
  ink: tokens.charcoal,
  text: tokens.charcoal,
  muted: tokens.pencil,
  accent: tokens.eagerGreen,
  accent2: tokens.sparkBlue,
  danger: "#ff4b4b",
  font: tokens.body,
  ...tokens,
};
