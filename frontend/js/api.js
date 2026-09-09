/**
 * Thin HTTP client for the CyclotorsionCheck backend.
 *
 * The SPA deliberately contains ZERO business logic for angle calculation or
 * safety validation (TDD Section 2.2) — all of that lives server-side. This
 * module only encodes how to talk to the API: attach the auth token, upload
 * the two images as multipart, and classify failures as either connection
 * problems or server errors (PRD Section 6.3 distinguishes these explicitly).
 */

import { CONFIG } from "./config.js";
import { getCurrentUserToken } from "./auth.js";

/** Normalised error with a machine-readable kind for the UI to branch on. */
export class ApiError extends Error {
  constructor(message, { kind = "server", status = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.kind = kind; // "connection" | "server" | "validation"
    this.status = status;
  }
}

async function bearerTokenOrThrow() {
  const token = await getCurrentUserToken();
  if (!token) {
    throw new ApiError("You must be signed in to perform this action.", { kind: "validation" });
  }
  return token;
}

/** POST the upright + rotated images to /detect and return the parsed result.
 *
 * @param {File} uprightFile
 * @param {File} rotatedFile
 * @param {{caseRef?: string, eyeLaterality?: string, targetAxisDeg?: number}} [context]
 *   Optional PRD Epic 6 case context: a pseudonymous facility case reference,
 *   an eye laterality (OD/OS), and the planned toric-IOL target axis (degrees).
 *   All are fully optional — the detection is unchanged when omitted.
 */
export async function detect(uprightFile, rotatedFile, context = {}) {
  const token = await bearerTokenOrThrow();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CONFIG.DETECT_TIMEOUT_MS);

  const form = new FormData();
  form.append("upright", uprightFile, uprightFile.name);
  form.append("rotated", rotatedFile, rotatedFile.name);
  // Epic 6 optional fields (US-6.1 / US-6.2 / US-6.3). Only send values that
  // are actually present so the backend sees an all-optional request.
  if (context.caseRef) form.append("case_ref", context.caseRef);
  if (context.eyeLaterality) form.append("eye_laterality", context.eyeLaterality);
  if (context.targetAxisDeg !== undefined && context.targetAxisDeg !== null) {
    form.append("target_axis_deg", String(context.targetAxisDeg));
  }
  if (context.patientId) form.append("patient_id", context.patientId);

  let response;
  try {
    response = await fetch(`${CONFIG.API_BASE}/detect`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") {
      throw new ApiError(
        "The request took too long and timed out. Check your connection and try again.",
        { kind: "connection" },
      );
    }
    // Network-level failure (offline, DNS, refused) -> connection problem.
    throw new ApiError("Connection issue — check your internet and try again", {
      kind: "connection",
    });
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    const detail = await safeDetail(response);
    if (response.status === 401 || response.status === 403) {
      throw new ApiError(detail || "Your session is invalid. Please sign in again.", {
        kind: "validation",
        status: response.status,
      });
    }
    throw new ApiError(detail || `Server error (${response.status})`, {
      kind: "server",
      status: response.status,
    });
  }
  return response.json();
}

/** GET /stats and return the aggregate, de-identified statistics. */
export async function fetchStats() {
  const token = await bearerTokenOrThrow();
  let response;
  try {
    response = await fetch(`${CONFIG.API_BASE}/stats`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new ApiError("Connection issue — check your internet and try again", {
      kind: "connection",
    });
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw new ApiError("Your session is invalid. Please sign in again.", {
        kind: "validation",
        status: response.status,
      });
    }
    throw new ApiError(`Server error (${response.status})`, {
      kind: "server",
      status: response.status,
    });
  }
  return response.json();
}

async function safeDetail(response) {
  try {
    const body = await response.json();
    return body && body.detail ? String(body.detail) : null;
  } catch {
    return null;
  }
}

/**
 * PRD Epic 7 client for patient-profile endpoints (US-7.1..7.6).
 * All requests carry the auth token and the backend enforces facility scoping.
 */

/** GET /patients/search?q=... — prefix search on name or MRN (US-7.2). */
export async function searchPatients(q, limit = 10) {
  const token = await bearerTokenOrThrow();
  const url = `${CONFIG.API_BASE}/patients/search?q=${encodeURIComponent(q)}&limit=${limit}`;
  let response;
  try {
    response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  } catch {
    throw new ApiError("Connection issue — check your internet and try again", {
      kind: "connection",
    });
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw new ApiError("Your session is invalid. Please sign in again.", {
        kind: "validation",
        status: response.status,
      });
    }
    throw new ApiError(`Server error (${response.status})`, {
      kind: "server",
      status: response.status,
    });
  }
  return response.json();
}

/**
 * GET /patients/suggest-mrn — a placeholder MRN the create form can pre-fill.
 * Purely a convenience default (US-7.1); the field stays editable and the
 * real hospital MRN can always be typed in instead.
 */
export async function suggestMrn() {
  const token = await bearerTokenOrThrow();
  let response;
  try {
    response = await fetch(`${CONFIG.API_BASE}/patients/suggest-mrn`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new ApiError("Connection issue — check your internet and try again", {
      kind: "connection",
    });
  }
  if (!response.ok) {
    throw new ApiError(`Server error (${response.status})`, {
      kind: "server",
      status: response.status,
    });
  }
  return response.json();
}

/**
 * POST /patients — create a profile with explicit consent (US-7.1 + US-7.4).
 * @param {{fullName: string, dateOfBirth: string, mrn: string, phone?: string}} payload
 */
export async function createPatient(payload) {
  const token = await bearerTokenOrThrow();
  const body = JSON.stringify({
    full_name: payload.fullName,
    date_of_birth: payload.dateOfBirth,
    mrn: payload.mrn,
    phone: payload.phone || undefined,
    consent: true, // explicit consent capture (US-7.4) asserted by this call
  });
  let response;
  try {
    response = await fetch(`${CONFIG.API_BASE}/patients`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body,
    });
  } catch {
    throw new ApiError("Connection issue — check your internet and try again", {
      kind: "connection",
    });
  }
  if (!response.ok) {
    const detail = await safeDetail(response);
    if (response.status === 409) {
      throw new ApiError(detail || "A patient with this MRN already exists.", {
        kind: "validation",
        status: response.status,
      });
    }
    throw new ApiError(detail || `Server error (${response.status})`, {
      kind: "server",
      status: response.status,
    });
  }
  return response.json();
}

/** GET /patients/{id} — one patient profile (US-7.3). */
export async function fetchPatient(patientId) {
  const token = await bearerTokenOrThrow();
  let response;
  try {
    response = await fetch(`${CONFIG.API_BASE}/patients/${encodeURIComponent(patientId)}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new ApiError("Connection issue — check your internet and try again", {
      kind: "connection",
    });
  }
  if (!response.ok) {
    throw new ApiError(`Could not load patient (${response.status})`, {
      kind: "server",
      status: response.status,
    });
  }
  return response.json();
}

/** GET /patients/{id}/cases — the case/result history for a patient (US-7.3). */
export async function fetchPatientCases(patientId) {
  const token = await bearerTokenOrThrow();
  let response;
  try {
    response = await fetch(`${CONFIG.API_BASE}/patients/${encodeURIComponent(patientId)}/cases`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new ApiError("Connection issue — check your internet and try again", {
      kind: "connection",
    });
  }
  if (!response.ok) {
    throw new ApiError(`Could not load patient history (${response.status})`, {
      kind: "server",
      status: response.status,
    });
  }
  return response.json();
}

/**
 * DELETE /patients/{id} — cascading right-to-erasure (US-7.5).
 * Facility-admin only; the backend orchestrates the cross-store cascade.
 */
export async function erasePatient(patientId) {
  const token = await bearerTokenOrThrow();
  let response;
  try {
    response = await fetch(`${CONFIG.API_BASE}/patients/${encodeURIComponent(patientId)}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new ApiError("Connection issue — check your internet and try again", {
      kind: "connection",
    });
  }
  if (!response.ok) {
    const detail = await safeDetail(response);
    throw new ApiError(detail || `Erasure failed (${response.status})`, {
      kind: "server",
      status: response.status,
    });
  }
  return response.json();
}

/** GET /admin/erasure-log — the PII-free erasure audit trail (US-7.5). */
export async function fetchErasureLog(limit = 50) {
  const token = await bearerTokenOrThrow();
  let response;
  try {
    response = await fetch(`${CONFIG.API_BASE}/admin/erasure-log?limit=${limit}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new ApiError("Connection issue — check your internet and try again", {
      kind: "connection",
    });
  }
  if (!response.ok) {
    throw new ApiError(`Server error (${response.status})`, {
      kind: "server",
      status: response.status,
    });
  }
  return response.json();
}
