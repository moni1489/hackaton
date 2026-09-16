/* Минималистичный набор иконок в едином штрихе (stroke 1.7). */
const S = ({ children, size = 18, fill = 'none', ...rest }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke="currentColor"
       strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...rest}>{children}</svg>
);

export const IcoMap = (p) => <S {...p}><path d="M9 3 3 6v15l6-3 6 3 6-3V3l-6 3-6-3Z" /><path d="M9 3v15M15 6v15" /></S>;
export const IcoSchool = (p) => <S {...p}><path d="M3 21h18M5 21V9l7-5 7 5v12" /><path d="M9 21v-5h6v5" /></S>;
export const IcoPc = (p) => <S {...p}><rect x="2" y="4" width="20" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></S>;
export const IcoAlert = (p) => <S {...p}><path d="M12 9v4M12 17h.01" /><path d="M10.3 3.9 2.4 17.5A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /></S>;
export const IcoGear = (p) => <S {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0-1.1-2.7H1a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 2.6 7a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 7 2.6h.1A1.6 1.6 0 0 0 9 1.1V1a2 2 0 1 1 4 0v.1A1.6 1.6 0 0 0 15 2.6a1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8v.1a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z" transform="scale(.86) translate(2 2)" /></S>;
export const IcoSearch = (p) => <S {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.2-3.2" /></S>;
export const IcoBell = (p) => <S {...p}><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.7 21a2 2 0 0 1-3.4 0" /></S>;
export const IcoLayers = (p) => <S {...p}><path d="m12 2 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5M3 17l9 5 9-5" /></S>;
export const IcoPulse = (p) => <S {...p}><path d="M3 12h4l3 8 4-16 3 8h4" /></S>;
export const IcoBrain = (p) => <S {...p}><path d="M9.5 3a3 3 0 0 0-3 3 3 3 0 0 0-1.5 5.5A3 3 0 0 0 7 17a3 3 0 0 0 5 2.2V4.5A2.5 2.5 0 0 0 9.5 3Z" /><path d="M14.5 3a3 3 0 0 1 3 3 3 3 0 0 1 1.5 5.5A3 3 0 0 1 17 17a3 3 0 0 1-5 2.2" /></S>;
export const IcoShield = (p) => <S {...p}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" /><path d="m9 12 2 2 4-4" /></S>;
export const IcoChart = (p) => <S {...p}><path d="M3 3v18h18" /><path d="M7 15v3M12 9v9M17 12v6" /></S>;
export const IcoDoc = (p) => <S {...p}><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7Z" /><path d="M14 2v5h5M9 13h6M9 17h6" /></S>;
export const IcoCal = (p) => <S {...p}><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 11h18" /></S>;
export const IcoDown = (p) => <S {...p}><path d="M12 4v14M6 13l6 6 6-6" /></S>;
export const IcoUp = (p) => <S {...p}><path d="M12 20V6M6 11l6-6 6 6" /></S>;
export const IcoChevron = (p) => <S {...p} size={14}><path d="m6 9 6 6 6-6" /></S>;
export const IcoClose = (p) => <S {...p}><path d="M18 6 6 18M6 6l12 12" /></S>;
export const IcoRefresh = (p) => <S {...p}><path d="M21 12a9 9 0 1 1-2.6-6.4" /><path d="M21 3v6h-6" /></S>;
export const IcoLogout = (p) => <S {...p}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5M21 12H9" /></S>;
export const IcoLeaf = (p) => <S {...p} size={19}><path d="M4 20c8 2 16-4 16-14-8-2-16 4-16 14Z" /><path d="M4 20c2-6 6-9 10-11" /></S>;
export const IcoSpark = (p) => <S {...p}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" /></S>;
export const IcoWifi = (p) => <S {...p}><path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01M2 9a15 15 0 0 1 20 0" /></S>;
export const IcoClock = (p) => <S {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></S>;
export const IcoTrend = (p) => <S {...p}><path d="m3 17 6-6 4 4 8-8" /><path d="M15 7h6v6" /></S>;
export const IcoUsers = (p) => <S {...p}><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.9" /></S>;
export const IcoQueue = (p) => <S {...p}><rect x="3" y="4" width="18" height="5" rx="1.5" /><rect x="3" y="12" width="18" height="5" rx="1.5" /><path d="M7 20h10" /></S>;
