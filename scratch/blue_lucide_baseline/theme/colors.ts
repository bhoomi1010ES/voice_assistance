export const colors = {
  light: {
    // Canvas & Surfaces
    background: '#FAF8FC',
    surface: '#FFFFFF',
    surfaceMuted: '#F4EFF7',
    surfaceLow: '#F7F3F9',
    surfaceHigh: '#EFEAF5',
    border: '#EFEAF5',
    borderSubtle: '#F5F0FA',

    // Primary Brand (Modern Violet / Purple)
    primary: '#7B61FF',
    primaryDark: '#5D45E0',
    primaryContainer: '#EEE8FF',
    onPrimary: '#FFFFFF',
    onPrimaryContainer: '#2B1480',

    // Secondary Accent (Coral / Pink)
    secondary: '#FF6584',
    secondaryLight: '#FF7D99',
    secondaryContainer: '#FFEAEF',
    onSecondary: '#FFFFFF',
    onSecondaryContainer: '#80102B',

    // Tertiary (Soft Lavender / Slate)
    tertiary: '#6C6377',
    tertiaryContainer: '#EAE5F2',
    onTertiary: '#FFFFFF',

    // Content / Text
    text: '#181725',
    textMuted: '#8E8B9E',
    textSubtle: '#A6A2B5',
    textInverse: '#FAF8FC',

    // Backward-compatible aliases for existing screens
    accent: '#7B61FF',
    accentText: '#FFFFFF',

    // Semantic States
    success: '#176B45',
    successContainer: '#D1F2DE',
    warning: '#B45309',
    warningContainer: '#FEF3C7',
    error: '#BA1A1A',
    errorContainer: '#FFDAD6',
    disabled: '#C4BFCE',

    // Voice Orb Identity
    orbGradientStart: '#7B61FF',
    orbGradientMiddle: '#E056FD',
    orbGradientEnd: '#FFA07A',
    orbGlow: 'rgba(123, 97, 255, 0.35)',
    orbRipple: 'rgba(224, 86, 253, 0.15)',
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
