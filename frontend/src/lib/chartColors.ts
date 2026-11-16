/**
 * Categorical chart colors, assigned in this fixed order and never cycled.
 * Checked for lightness, chroma, color-vision-deficiency separation and 3:1
 * contrast against the white card surface.
 */
export const CHART_COLORS = {
  teal: '#0f9f97',
  blue: '#4a6fd0',
  amber: '#c9861a',
  /** Reserved for failed / rejected counts; always paired with a text label. */
  critical: '#e0526a',
} as const
