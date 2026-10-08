import React from 'react';
import Activity from 'lucide-react-native/icons/activity';
import AudioLines from 'lucide-react-native/icons/audio-lines';
import Bell from 'lucide-react-native/icons/bell';
import BellOff from 'lucide-react-native/icons/bell-off';
import Brain from 'lucide-react-native/icons/brain';
import CalendarCheck from 'lucide-react-native/icons/calendar-check';
import Check from 'lucide-react-native/icons/check';
import ChevronDown from 'lucide-react-native/icons/chevron-down';
import ChevronLeft from 'lucide-react-native/icons/chevron-left';
import ChevronRight from 'lucide-react-native/icons/chevron-right';
import ChevronUp from 'lucide-react-native/icons/chevron-up';
import Circle from 'lucide-react-native/icons/circle';
import CircleCheck from 'lucide-react-native/icons/circle-check';
import CircleX from 'lucide-react-native/icons/circle-x';
import Clock from 'lucide-react-native/icons/clock';
import Database from 'lucide-react-native/icons/database';
import Eye from 'lucide-react-native/icons/eye';
import Heart from 'lucide-react-native/icons/heart';
import Info from 'lucide-react-native/icons/info';
import Laptop from 'lucide-react-native/icons/laptop';
import Lightbulb from 'lucide-react-native/icons/lightbulb';
import Lock from 'lucide-react-native/icons/lock';
import LogOut from 'lucide-react-native/icons/log-out';
import Mail from 'lucide-react-native/icons/mail';
import MapPin from 'lucide-react-native/icons/map-pin';
import MessageCircle from 'lucide-react-native/icons/message-circle';
import Mic from 'lucide-react-native/icons/mic';
import Network from 'lucide-react-native/icons/network';
import Pencil from 'lucide-react-native/icons/pencil';
import Plane from 'lucide-react-native/icons/plane';
import Plus from 'lucide-react-native/icons/plus';
import RotateCcw from 'lucide-react-native/icons/rotate-ccw';
import Search from 'lucide-react-native/icons/search';
import Send from 'lucide-react-native/icons/send';
import Settings from 'lucide-react-native/icons/settings';
import Shield from 'lucide-react-native/icons/shield';
import ShoppingCart from 'lucide-react-native/icons/shopping-cart';
import Sparkles from 'lucide-react-native/icons/sparkles';
import SquareCheck from 'lucide-react-native/icons/square-check';
import Trash2 from 'lucide-react-native/icons/trash';
import TriangleAlert from 'lucide-react-native/icons/triangle-alert';
import User from 'lucide-react-native/icons/user';
import X from 'lucide-react-native/icons/x';
import Zap from 'lucide-react-native/icons/zap';
import { useAppTheme } from '../../design/ThemeProvider';

const icons = {
  Activity,
  AudioLines,
  Bell,
  BellOff,
  Brain,
  CalendarCheck,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Circle,
  CircleCheck,
  CircleX,
  Clock,
  Database,
  Eye,
  Heart,
  Info,
  Laptop,
  Lightbulb,
  Lock,
  LogOut,
  Mail,
  MapPin,
  MessageCircle,
  Mic,
  Network,
  Pencil,
  Plane,
  Plus,
  RotateCcw,
  Search,
  Send,
  Settings,
  Shield,
  ShoppingCart,
  Sparkles,
  SquareCheck,
  Trash2,
  TriangleAlert,
  User,
  X,
  Zap,
};

export type AppIconName = keyof typeof icons;

export function AppIcon({
  name,
  size = 22,
  color,
  strokeWidth = 2,
}: {
  name: AppIconName;
  size?: number;
  color?: string;
  strokeWidth?: number;
}) {
  const { colors } = useAppTheme();
  const Icon = icons[name];
  return (
    <Icon
      accessible={false}
      size={size}
      color={color ?? colors.primary}
      strokeWidth={strokeWidth}
    />
  );
}

const glyphNames: Record<string, AppIconName> = {
  '🎙': 'Mic',
  '🎙️': 'Mic',
  '🎤': 'Mic',
  '🧠': 'Brain',
  '☑': 'SquareCheck',
  '⚙': 'Settings',
  '⚙️': 'Settings',
  '✦': 'Sparkles',
  '✨': 'Sparkles',
  '◉': 'AudioLines',
  ııllıl: 'AudioLines',
  '💬': 'MessageCircle',
  '👤': 'User',
  '✉': 'Mail',
  '🔒': 'Lock',
  '🔐': 'Shield',
  '📱': 'Laptop',
  '🥞': 'Database',
  '🔍': 'Search',
  '🔍✨': 'Search',
  '🕸️': 'Network',
  '💡': 'Lightbulb',
  '⚠️': 'TriangleAlert',
  '❤️': 'Heart',
  '🖥️': 'Laptop',
  '✈️': 'Plane',
  '🛒': 'ShoppingCart',
  '👁️‍🗨️': 'Eye',
  '👁️': 'Eye',
  '✏️': 'Pencil',
  '🗑️': 'Trash2',
  '🔔': 'Bell',
  '✕': 'X',
  '×': 'X',
  '✓': 'Check',
  '✗': 'X',
  '›': 'ChevronRight',
  '‹': 'ChevronLeft',
  '⌃': 'ChevronUp',
  '⌵': 'ChevronDown',
  '↺': 'RotateCcw',
  '←': 'ChevronLeft',
  '○': 'Circle',
  '◷': 'Clock',
  '⌾': 'MapPin',
  'ⓘ': 'Info',
  '🔕': 'BellOff',
  ℹ️: 'Info',
  '📋': 'SquareCheck',
  '⚡': 'Zap',
  '🗑': 'Trash2',
};

/** Bridge category/field icon metadata to the shared Lucide icon set. */
export function GlyphIcon({
  glyph,
  ...props
}: Omit<React.ComponentProps<typeof AppIcon>, 'name'> & { glyph: string }) {
  return <AppIcon {...props} name={glyphNames[glyph] ?? 'Circle'} />;
}
