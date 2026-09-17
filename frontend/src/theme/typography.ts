export const typography = {
  // Scales (px)
  displayLg: 36,
  display: 32,
  title: 28,
  titleLg: 30,
  heading: 22,
  subheading: 18,
  body: 16,
  bodySm: 14,
  label: 14,
  labelSm: 12,
  caption: 12,

  // Font weights
  weights: {
    regular: '400' as const,
    medium: '500' as const,
    semibold: '600' as const,
    bold: '700' as const,
  },

  // Line heights
  lineHeights: {
    display: 42,
    title: 34,
    heading: 28,
    body: 24,
    bodySm: 20,
    caption: 16,
  },
} as const;
