export const colors = {
  light: {
    // Canvas & Surfaces
    background: '#FCF8FB',
    surface: '#FFFFFF',
    surfaceMuted: '#F0EDEF',
    surfaceLow: '#F6F3F5',
    surfaceHigh: '#EAE7EA',
    border: '#E0C0B4',
    borderSubtle: '#F0EDEF',

    // Primary Brand (Stitch Terracotta / Amber)
    primary: '#D95C23',
    primaryDark: '#A23900',
    primaryContainer: '#FFDBCE',
    onPrimary: '#FFFFFF',
    onPrimaryContainer: '#7F2B00',

    // Secondary Accent (Stitch Deep Indigo / Violet)
    secondary: '#5647CA',
    secondaryLight: '#6355D8',
    secondaryContainer: '#E4DFFF',
    onSecondary: '#FFFFFF',
    onSecondaryContainer: '#160066',

    // Tertiary (Warm Sand / Neutral Bronze)
    tertiary: '#5E5C53',
    tertiaryContainer: '#E7E2D6',
    onTertiary: '#FFFFFF',

    // Content / Text
    text: '#1B1B1D',
    textMuted: '#584239',
    textSubtle: '#8C7168',
    textInverse: '#FCF8FB',

    // Backward-compatible aliases for existing screens
    accent: '#D95C23',
    accentText: '#FFFFFF',

    // Semantic States
    success: '#176B45',
    successContainer: '#D1F2DE',
    warning: '#B45309',
    warningContainer: '#FEF3C7',
    error: '#BA1A1A',
    errorContainer: '#FFDAD6',
    disabled: '#A8B0BA',

    // Voice Orb Identity
    orbGradientStart: '#FF9666',
    orbGradientMiddle: '#E05B28',
    orbGradientEnd: '#9C3A12',
    orbGlow: 'rgba(217, 92, 35, 0.35)',
    orbRipple: 'rgba(217, 92, 35, 0.12)',
  },
  dark: {
    // Canvas & Surfaces
    background: '#161618',
    surface: '#1E1E20',
    surfaceMuted: '#27272A',
    surfaceLow: '#202022',
    surfaceHigh: '#323236',
    border: '#3F3F46',
    borderSubtle: '#27272A',

    // Primary Brand (Stitch Terracotta Warmth)
    primary: '#FFB599',
    primaryDark: '#D95C23',
    primaryContainer: '#7F2B00',
    onPrimary: '#370E00',
    onPrimaryContainer: '#FFDBCE',

    // Secondary Accent
    secondary: '#C6BFFF',
    secondaryLight: '#6F61E4',
    secondaryContainer: '#402DB4',
    onSecondary: '#160066',
    onSecondaryContainer: '#E4DFFF',

    // Tertiary
    tertiary: '#CBC6BB',
    tertiaryContainer: '#49473E',
    onTertiary: '#1D1C14',

    // Content / Text
    text: '#F4F4F5',
    textMuted: '#A1A1AA',
    textSubtle: '#71717A',
    textInverse: '#161618',

    // Backward-compatible aliases for existing screens
    accent: '#FFB599',
    accentText: '#370E00',

    // Semantic States
    success: '#7DDBAB',
    successContainer: '#064E3B',
    warning: '#F6C96B',
    warningContainer: '#78350F',
    error: '#FFB4AB',
    errorContainer: '#7F1D1D',
    disabled: '#52525B',

    // Voice Orb Identity
    orbGradientStart: '#FF9666',
    orbGradientMiddle: '#E05B28',
    orbGradientEnd: '#9C3A12',
    orbGlow: 'rgba(255, 181, 153, 0.35)',
    orbRipple: 'rgba(255, 181, 153, 0.15)',
  },
} as const;

export type ThemeMode = keyof typeof colors;
export type ColorTokens = (typeof colors)[ThemeMode];
