/**
 * Shared DOM + image helpers for the SPA.
 *
 * Includes the client-side image-quality heuristic for PRD US-1.3: before
 * calculation, check the uploaded images for signs of being blurry, near-black,
 * or not eye-like, and warn the surgeon (non-blocking) without preventing them
 * from proceeding.
 */

import { CONFIG } from "./config.js";

const SVG_NS = "http://www.w3.org/2000/svg";

// Minimal inline icon set (outline style, 24x24 viewBox). Kept inline rather
// than pulled from a CDN icon font/library so the app has zero extra network
// dependency for icons — relevant given the PRD's low-connectivity pilot
// sites (Section 6) and the desire for consistent rendering across the
// varied low-cost Android tablets these hospitals actually deploy.
const ICON_PATHS = {
  eye: "M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z|M15 12a3 3 0 11-6 0 3 3 0 016 0z",
  warning:
    "M12 9v3.75m0 3.75h.007M4.318 18.5h15.364c1.53 0 2.485-1.667 1.72-2.996L13.72 4.005c-.765-1.33-2.675-1.33-3.44 0L2.598 15.504C1.833 16.833 2.788 18.5 4.318 18.5z",
  "check-circle": "M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
  camera:
    "M6.827 6.175A2.31 2.31 0 015.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.87 47.87 0 00-1.134-.175 2.31 2.31 0 01-1.64-1.055l-.822-1.316a2.192 2.192 0 00-1.736-1.039 48.774 48.774 0 00-5.232 0 2.192 2.192 0 00-1.736 1.039l-.821 1.316z|M16.5 12.75a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0z",
  upload:
    "M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 8.25L12 3.75m0 0L7.5 8.25M12 3.75v13.5",
  menu: "M3.75 6.75h16.5M3.75 12h16.5M3.75 17.25h16.5",
  refresh:
    "M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99",
  chevron: "M19.5 8.25l-7.5 7.5-7.5-7.5",
  trash:
    "M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0",
};

/**
 * Build a small inline SVG icon. Decorative by default (aria-hidden) since
 * icons here always accompany visible text; pass `label` + `decorative:
 * false` for an icon that is the only accessible name for a control.
 */
export function icon(name, { size = 20, decorative = true, label } = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.classList.add("icon");
  if (!decorative && label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  const segments = (ICON_PATHS[name] || "").split("|");
  for (const d of segments) {
    if (!d) continue;
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
  }
  return svg;
}

/** Create an element with optional attributes, classes, and children. */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("data-")) node.setAttribute(k, v);
    else if (k === "html") throw new Error("el() forbids HTML injection; use textContent instead");
    else node.setAttribute(k, v);
  }
  for (const child of [].concat(children)) {
    if (child) node.appendChild(child);
  }
  return node;
}

/** Render a neutral/zero state when required values are absent. */
export function dash(value) {
  return value === null || value === undefined ? "—" : value;
}

/**
 * Client-side image quality heuristic (PRD US-1.3).
 *
 * @param {File} file - The uploaded image file.
 * @returns {Promise<{ok: boolean, reason?: string}>}
 *   ok=true when the image looks usable; ok=false with a reason when a
 *   non-blocking quality warning should be shown.
 */
export async function checkImageQuality(file) {
  let bitmap = null;
  try {
    bitmap = await createImageBitmap(file);
    const w = bitmap.width;
    const h = bitmap.height;
    if (w < 40 || h < 40) {
      return { ok: false, reason: "Image is very small — may be too low resolution." };
    }
    const canvas = document.createElement("canvas");
    // Downscale to a small workable size for the heuristic.
    const scale = Math.min(1, 96 / Math.max(w, h));
    canvas.width = Math.max(1, Math.round(w * scale));
    canvas.height = Math.max(1, Math.round(h * scale));
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;

    const n = canvas.width * canvas.height;
    let sum = 0;
    let sumSq = 0;
    const gray = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const r = data[i * 4];
      const g = data[i * 4 + 1];
      const b = data[i * 4 + 2];
      const v = 0.299 * r + 0.587 * g + 0.114 * b;
      gray[i] = v;
      sum += v;
      sumSq += v * v;
    }
    const mean = sum / n;
    const variance = sumSq / n - mean * mean;
    const std = Math.max(0, Math.sqrt(variance));

    // Near-black (extremely underexposed / black frame).
    if (mean < 18) {
      return { ok: false, reason: "Image appears nearly black." };
    }
    // Blurry heuristic: near-zero contrast variance across the frame.
    if (std < 8) {
      return { ok: false, reason: "Image may be blurry or uniform (~low detail)." };
    }
    return { ok: true };
  } catch {
    // If we can't decode/analyze (e.g. a corrupt file), flag it.
    return { ok: false, reason: "Image could not be read — it may be corrupted." };
  } finally {
    // Release the decoded bitmap on EVERY path (incl. early returns and
    // exceptions) so a large ImageBitmap isn't leaked per upload (architecture
    // review Minor). createImageBitmap failures leave bitmap null — safe to close.
    if (bitmap) bitmap.close();
  }
}

/** Validate a file against the size + MIME limits (PRD US-1.1). */
export function validateImageFile(file) {
  if (!CONFIG.ALLOWED_MIME_TYPES.includes(file.type)) {
    return { ok: false, reason: `Unsupported file type "${file.type}". Use JPEG, PNG, or WebP.` };
  }
  if (file.size > CONFIG.MAX_IMAGE_BYTES) {
    return { ok: false, reason: "File exceeds the 10 MB size limit." };
  }
  if (file.size === 0) {
    return { ok: false, reason: "File is empty." };
  }
  return { ok: true };
}

// DOB-like: DD/MM/YYYY, D-M-YY, or ISO YYYY-MM-DD. A case_ref is meant to be a
// pseudonymous facility code (PRD Epic 6, US-6.4); a date-like value is a strong
// signal of an accidental PII entry.
const DOB_RE = /\b(?:\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\b/;

// Name-like: two or more space-separated capitalised words with lowercase
// letters (e.g. "Anjali Rao"), the risky case of pasting a patient's name.
const NAME_RE = /\b[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?(?: [A-Z][a-z]+){1,}\b/;

/**
 * US-6.4 safety-nudge check: does a case reference look like it might contain a
 * patient's real name or date of birth?
 *
 * Mirrors the backend's `looks_like_pii` (defense in depth). This is a
 * lightweight pattern check / safety nudge, NOT a hard technical guarantee —
 * the facility's own data-handling policy remains the primary control. Values
 * that look like a DOB or a two-capitalised-word name return true so the UI can
 * ask for confirmation before proceeding.
 */
export function looksLikePII(value) {
  if (!value) return false;
  const text = String(value).trim();
  if (!text) return false;
  if (DOB_RE.test(text)) return true;
  if (NAME_RE.test(text) && /[a-z]/.test(text)) return true;
  return false;
}
