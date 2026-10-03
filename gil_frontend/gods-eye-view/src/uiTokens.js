/**
 * UI token helpers for God's Eye View.
 *
 * IMPORTANT: Must be safe to import in tests/node (no `window` required).
 */

export function cssVar(name, fallback = '') {
  try {
    if (typeof window === 'undefined' || typeof document === 'undefined') return fallback;
    const val = window.getComputedStyle(document.documentElement).getPropertyValue(name);
    const trimmed = String(val || '').trim();
    return trimmed || fallback;
  } catch {
    return fallback;
  }
}

export function hudAccentCss() {
  // Matches `style.css` :root `--hud-accent` default.
  return cssVar('--hud-accent', '#49a3f1');
}

