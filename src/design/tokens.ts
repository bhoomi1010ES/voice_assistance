export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
} as const;

export const radii = {
  sm: 8,
  md: 14,
  lg: 24,
  pill: 999,
} as const;

export const typography = {
  title: 30,
  heading: 22,
  body: 16,
  label: 14,
  caption: 12,
} as const;

export const colors = {
  light: {
    background: '#F7F8FA',
    surface: '#FFFFFF',
    surfaceMuted: '#EEF1F5',
    text: '#17202A',
    textMuted: '#5D6875',
    border: '#D7DDE5',
    accent: '#3157D5',
    accentText: '#FFFFFF',
    success: '#176B45',
    warning: '#805A00',
    error: '#B42318',
    disabled: '#A8B0BA',
  },
  dark: {
    background: '#11151B',
    surface: '#1B222B',
    surfaceMuted: '#27313D',
    text: '#F3F6FA',
    textMuted: '#B7C0CB',
    border: '#3B4755',
    accent: '#9CB2FF',
    accentText: '#11151B',
    success: '#7DDBAB',
    warning: '#F6C96B',
    error: '#FF9B91',
    disabled: '#697381',
  },
} as const;

export type ThemeMode = keyof typeof colors;
export type ColorTokens = (typeof colors)[ThemeMode];
