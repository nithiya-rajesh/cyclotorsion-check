# CyclotorsionCheck — Product Requirements Document

## Table of Contents

1. [Product Vision & Strategic Overview](#section-1-product-vision--strategic-overview)
   - 1.1 Executive Summary
   - 1.2 Problem Statement & Market Context
   - 1.3 Target Users & Personas
   - 1.4 Product Vision Statement
   - 1.5 Regulatory & Compliance Positioning
   - 1.6 Impact Metrics / North Star KPIs
   - 1.7 Out of Scope for V1
2. [Epics & User Stories](#section-2-epics--user-stories)
3. [Government Adoption & Distribution Model](#section-3-government-adoption--distribution-model)
4. [Clinical Validation Pathway](#section-4-clinical-validation-pathway)
5. [Data Residency & Cloud Region Specificity](#section-5-data-residency--cloud-region-specificity)
6. [Low-Connectivity / Offline Resilience](#section-6-low-connectivity--offline-resilience)
7. [Risk Register](#section-7-risk-register)
8. [Phased Roadmap Beyond V1](#section-8-phased-roadmap-beyond-v1)
9. [Alignment with Government Health Schemes](#section-9-alignment-with-government-health-schemes)
- [Document Status & Next Steps](#document-status--next-steps)

---

## Section 1: Product Vision & Strategic Overview

---

### 1.1 Executive Summary

CyclotorsionCheck is a low-cost, AI-powered clinical decision-support tool that helps
ophthalmic surgeons in Indian government hospitals verify precise toric intraocular
lens (IOL) alignment during cataract surgery, without the ₹1 crore+ (US $100,000+)
digital marker hardware currently required to do this reliably.

The product is conceived and developed as a **non-profit, portfolio-driven public
health initiative** — not a commercial venture. Its objective is to demonstrate that
a free-tier cloud AI stack (Google Gemini, Cloud Run, BigQuery) can close a specific,
well-documented precision gap in India's public cataract surgery program, where cost
— not clinical skill — is the primary barrier to adopting existing digital solutions.

> **Positioning statement:** CyclotorsionCheck is a screening and verification aid
> for surgeons — it augments clinical judgment, it does not replace it, and it is
> not intended to operate as an autonomous diagnostic or therapeutic device.

---

### 1.2 Problem Statement & Market Context

**The clinical problem.**
Patients undergoing cataract surgery with co-existing astigmatism require a toric
IOL, precisely rotated to a target axis to correct their vision. Between the
pre-operative measurement (patient seated upright) and the operating table (patient
supine), the eye undergoes natural **cyclotorsion** — a rotation of 2°–15° caused by
the change in gravitational reference. Every 1° of resulting lens misalignment
degrades astigmatic correction by approximately 3.3%; a 10° error can cause over 30%
loss of correction, often necessitating a second surgery.

**How this is handled today.**
An estimated 80% of surgeons globally, and the large majority of surgeons operating
in India's public hospital system, compensate for cyclotorsion using a manual ink
marker applied to the sclera while the patient is seated. This mark frequently
smudges under surgical microscope light and tear-film moisture, and lacks the
precision needed for sub-degree accuracy. Commercial digital alternatives exist
(e.g., Alcon VERION, Zeiss CALLISTO) but are priced well beyond the procurement
budget of public hospitals.

**Why this matters at India's scale.**
India performs among the highest volumes of cataract surgery in the world —
approximately 6–8 million procedures annually under the National Programme for
Control of Blindness & Visual Impairment (NPCB&VI — the program's current name;
referred to as "NPCB" in some older literature and by that shorthand elsewhere
in this document) and allied state programs. [1] Government-sector
ophthalmic units, which serve the largest share of India's economically
disadvantaged and rural population, predominantly rely on Manual Small Incision
Cataract Surgery (MSICS) and comparable cost-efficient techniques precisely because
premium instrumentation is financially inaccessible. [2] This creates a structural
gap: the patients most dependent on public healthcare are also the least likely to
receive precision-verified toric IOL alignment — despite toric lenses themselves
becoming more affordable and more frequently indicated.

**The opportunity.**
A camera, an internet connection, and a free-tier cloud AI account are already more
accessible in most Indian government hospitals than a $100,000 VERION unit.
CyclotorsionCheck tests whether that gap can be meaningfully closed with software
alone.

*[1], [2]: Figures drawn from published cataract-surgery-volume reporting and
government ophthalmology service literature; to be replaced with NPCB's most
current official statistics and a specific citation set before external
publication of this PRD.*

---

### 1.3 Target Users & Personas

| Persona | Role | Primary Need | Relationship to Product |
|---|---|---|---|
| **Dr. Anjali Rao** | Ophthalmic surgeon, District Government Hospital | Fast, reliable axis verification that fits into an already-compressed surgical schedule (often 15–20 procedures/day in high-volume public units) | **Primary end user.** Uses the capture + result screen directly, pre-op and/or in the OT anteroom. |
| **Mr. Suresh Iyer** | Biomedical Equipment Officer / Hospital Procurement Lead | A solution that is low/no-cost to acquire, has minimal IT/maintenance burden, and can be justified to state health department auditors | **Adoption gatekeeper.** Does not use the tool clinically but approves its deployment on hospital infrastructure. |
| **Dr. Kavitha Menon** | State NPCB Program Officer / District Health Administrator | Aggregate visibility into surgical quality metrics across facilities, and alignment with existing national blindness-control reporting structures | **Institutional sponsor / secondary stakeholder.** Interested in the Insights/analytics layer, not the per-patient workflow. |

---

### 1.4 Product Vision Statement

> *To make sub-degree toric IOL alignment verification a standard, zero-cost
> capability in every Indian government hospital performing cataract surgery —
> replacing the ink pen with a camera and closing a precision gap that today exists
> only because of hardware cost, not clinical necessity.*

---

### 1.5 Regulatory & Compliance Positioning

**Governing frameworks (India-specific — not HIPAA/GDPR):**

- **CDSCO Medical Device Rules, 2017** — governs Software as a Medical Device
  (SaMD) classification in India. CyclotorsionCheck V1 is deliberately positioned
  as a **Class A / non-diagnostic clinical decision-support tool**: it presents a
  calculated angle for the surgeon's independent clinical verification and does
  not autonomously drive a treatment decision. This positioning is a strategic
  choice to avoid the higher-risk Class B–D SaMD pathway during the portfolio/pilot
  phase; escalation to a higher classification is addressed in the Phased Roadmap
  (Section 8).
- **Digital Personal Data Protection (DPDP) Act, 2023** — governs handling of
  personal data, including patient photographs and, as of Epic 7 (Patient
  Profile), patient identity fields (name, date of birth, hospital MRN). Eye
  images themselves remain processed transiently and are never persisted
  (Section 5.3 — unchanged by Epic 7). Patient profile data **is** now
  persisted, which makes CyclotorsionCheck a DPDP "Data Fiduciary" with
  respect to that data: explicit consent capture (Epic 7, US-7.4), purpose
  limitation, and a real cascading right-to-erasure (US-7.5) are Must-have,
  implemented requirements, not optional hardening.
- **ICMR National Ethical Guidelines for Biomedical Research** — applicable once
  real clinical images (rather than synthetic data) are used for validation;
  governs the Institutional Ethics Committee (IEC) approval process (see Section 4,
  Clinical Validation Pathway).

> **Updated by Epic 7 (Patient Profile):** the positioning above originally
> assumed zero patient-identifiable data at rest anywhere in the system. That
> assumption no longer holds — Epic 7 stores real patient name, date of
> birth, and MRN by explicit product decision, because a tool with no
> patient record has limited real-world clinical utility (a result an
> attending surgeon cannot later find against "Mrs. Lakshmi's left eye" is
> of little use as a hospital record). This does **not** change the CDSCO
> Class A / non-diagnostic classification stated above — that classification
> concerns the software's medical decision-support function, not its data
> storage model — but it does materially raise the DPDP Act compliance bar
> from "largely moot, no PII exists" to "live, must be actively honored."
> Every section below that referenced the old zero-PII assumption is updated
> alongside this Epic (Sections 1.6, 1.7, 5.3, 5.5, the Risk Register, and
> Epic 5) rather than left standing in silent contradiction.

**Explicit non-goals for V1:** ABDM/FHIR-based EHR interoperability, HL7 messaging,
and CDSCO Class B+ certification are **not** in scope for the initial release (see
Section 1.7, Out of Scope).

---

### 1.6 Impact Metrics / North Star KPIs

Framed as public-health impact metrics, consistent with the project's non-profit
orientation rather than commercial KPIs:

| Metric | Definition | V1 Target |
|---|---|---|
| **Clinical accuracy on real images** | Mean Absolute Error (MAE) between AI-detected rotation and clinician-verified ground truth, on real (non-synthetic) clinical eye photographs | Match or improve on synthetic-data benchmark (~0.14° MAE) |
| **Facilities piloted** | Number of government hospitals actively using the tool in a clinical or shadow-mode capacity | 1–3 pilot sites |
| **Cases screened** | Number of individual patient cases where the tool was used for axis verification | Baseline volume tracked from first pilot deployment |
| **Estimated second-surgery reduction** | Modeled reduction in astigmatic under-correction requiring surgical revision, based on measured accuracy improvement over ink-marking baseline | Directional/qualitative for V1; quantified in later phases with real outcome data |
| **Patient data protection compliance** | Compliance metric — confirms every stored patient profile has documented DPDP-compliant consent, and that access is strictly facility-scoped | 100% of profiles consented; 0 cross-facility access incidents (hard requirement, not aspirational) |

> **Definition, updated with Epic 6 (Case Reference):** "patient-identifiable"
> means any data field that, on its own, reveals a specific patient's real
> identity — name, date of birth, national/health ID, or similar. A
> facility-generated pseudonymous case code (Epic 6) is explicitly **not**
> patient-identifiable under this definition, since it carries no meaning
> outside the facility's own separately-held patient chart. This hard
> requirement is unchanged by Epic 6 - it is defined more precisely because
> of it.
>
> **Superseded by Epic 7 (Patient Profile):** the original "Zero
> patient-identifiable data retained" KPI (100%) is no longer an accurate
> metric, because Epic 7 deliberately stores real patient identity fields.
> Rather than silently drop a KPI that was explicitly labeled "hard
> requirement, not aspirational," it is replaced above with the compliance
> metric that actually governs identified data going forward: consent
> coverage and facility-access isolation. **What has not changed:** eye
> images remain zero-persistence (Section 5.3) — only the identity-retention
> KPI's framing has changed, not the transient-image-handling guarantee.

---

### 1.7 Out of Scope for V1

To keep V1 achievable and honest about its limitations, the following are
**explicitly excluded** from this release:

- EHR/ABDM/FHIR/HL7 integration of any kind
- CDSCO Class B or higher certification / regulatory submission
- Any persistent storage of patient eye images (unchanged — see Section 5.3).
  **Note (Epic 7):** patient identity fields (name, date of birth, MRN) are
  now explicitly *in scope* via the Patient Profile epic — this exclusion
  covers images only, not identity data
- Multi-country or non-India deployment
- Automated/autonomous surgical guidance (the product informs; it does not act)
- Formal, statistically powered clinical trial (V1 targets a small-sample pilot
  validation, not a registration-grade study)
- Offline/edge inference (addressed as a future consideration in Sections 6.4 and 8.4)
- Any commercial pricing, billing, or licensing infrastructure

---

*End of Section 1.*

---

## Section 2: Epics & User Stories

This section translates the vision into buildable, testable units of work. Each
Epic maps to a distinct capability; each User Story follows the standard
`As a / I want / So that` format; each story carries Gherkin-style Acceptance
Criteria (`Given / When / Then`) to remove ambiguity at implementation and QA time.

Epics are sequenced in the order a surgeon would actually encounter them during a
single patient episode, followed by the cross-cutting compliance epic that governs
all of them.

---

### Epic 1 — Pre-Op & Intra-Op Image Capture

**Epic Goal:** Allow a surgeon or clinical assistant to capture the two reference
photographs (upright, supine) required for cyclotorsion calculation, with minimal
workflow friction in a high-volume surgical day.

| ID | User Story | Priority |
|---|---|---|
| **US-1.1** | As **Dr. Anjali Rao (surgeon)**, I want to upload an "upright" eye photo taken in the pre-op holding area, so that the system has a baseline reference before the patient is repositioned. | Must-have |
| **US-1.2** | As **Dr. Anjali Rao**, I want to upload a "supine" eye photo taken immediately before the surgical incision, so that the system can measure the actual rotation that occurred. | Must-have |
| **US-1.3** | As **Dr. Anjali Rao**, I want clear visual confirmation that both photos were received and are legible, so that I don't proceed into surgery on a failed capture. | Must-have |
| **US-1.4** | As a **clinical assistant**, I want to capture and upload on behalf of the surgeon, so that the surgeon's hands remain free for patient preparation. | Should-have |

**Acceptance Criteria — US-1.1 / US-1.2**
```gherkin
Given the surgeon is on the Analyze screen
When they select or capture an eye photo for the "upright" or "supine" slot
Then the image is accepted only if it is a valid image file under the defined
  size limit (e.g. 10MB)
And a thumbnail preview renders immediately in that slot
And the "Calculate rotation" action remains disabled until both slots are filled
```

**Acceptance Criteria — US-1.3**
```gherkin
Given both images have been uploaded
When the images are visibly blurry, near-black, or not eye-shaped
  (basic client-side quality heuristic)
Then the system displays a non-blocking warning: "Image quality may affect
  accuracy — consider retaking" before allowing calculation to proceed
```

---

### Epic 2 — AI-Assisted Landmark Detection & Angle Calculation

**Epic Goal:** Reliably compute the cyclotorsion angle between the two images using
Gemini-based landmark detection combined with deterministic trigonometry — not an
AI-estimated angle — per the design rationale established during prototyping.

| ID | User Story | Priority |
|---|---|---|
| **US-2.1** | As **Dr. Anjali Rao**, I want the system to identify a stable anatomical landmark in both photos automatically, so that I don't have to manually mark reference points myself. | Must-have |
| **US-2.2** | As the **system**, I want to calculate rotation using deterministic trigonometry on the landmark's coordinates (not an AI-estimated angle), so that results are mathematically reproducible and auditable. | Must-have |
| **US-2.3** | As **Dr. Anjali Rao**, I want to see the calculated angle within a clinically usable timeframe, so that this doesn't slow down my surgical schedule. | Must-have |

**Acceptance Criteria — US-2.1 / US-2.2**
```gherkin
Given two valid eye images have been submitted
When the detection pipeline runs
Then a single landmark (e.g. scleral mark, vessel bifurcation) is identified in
  both images with returned coordinates
And the rotation angle is computed via arctan2 trigonometry against those
  coordinates and a defined center point — never returned directly from the
  language model as a free-form angle estimate
And the result is returned with the calculated angle in degrees, to two
  decimal places
```

**Acceptance Criteria — US-2.3**
```gherkin
Given a valid detection request
When the system is under normal (non-degraded) operating conditions
Then a result is returned within 10 seconds in the 95th percentile of requests
And if the underlying AI provider reports transient overload, the system
  retries automatically up to 3 times before surfacing an error to the user
```

---

### Epic 3 — Clinical Safety & Result Validation

**Epic Goal:** Prevent physically implausible results from being presented to the
surgeon as if they were trustworthy, and make the tool's confidence — and its
limitations — visible rather than implicit.

| ID | User Story | Priority |
|---|---|---|
| **US-3.1** | As **Dr. Anjali Rao**, I want implausible results (e.g. >20° or ~0°) automatically flagged, so that I know to disregard or manually re-verify rather than trust a bad reading. | Must-have |
| **US-3.2** | As **Dr. Anjali Rao**, I want an explicit, persistent on-screen disclaimer that this is a decision-support aid and not a substitute for clinical judgment, so that liability and appropriate use are unambiguous every time I use it. | Must-have |
| **US-3.3** | As **Mr. Suresh Iyer (procurement)**, I want documented evidence that safety flagging logic exists and is tested, so that I can defend this tool's inclusion in hospital procurement review. | Should-have |

**Acceptance Criteria — US-3.1**
```gherkin
Given a rotation angle has been calculated
When the absolute value of the angle exceeds 20 degrees
Then the result is labeled "Flagged — unusually large, verify manually" in the
  UI, rendered in a visually distinct (warning) state, not the default success state

Given a rotation angle has been calculated
When the absolute value of the angle is less than 0.1 degrees
Then the result is labeled "Flagged — angle near zero, confirm photos are
  distinct" in the same warning state
```

**Acceptance Criteria — US-3.2**
```gherkin
Given any authenticated user is on the Analyze screen
When the screen renders
Then a disclaimer banner stating the tool's non-diagnostic, decision-support
  status is visible without requiring a scroll or click
And this banner cannot be permanently dismissed for the session
```

---

### Epic 4 — Insights & Aggregate Analytics

**Epic Goal:** Give both individual surgeons and institutional stakeholders
visibility into usage and outcome trends, supporting both point-of-care trust and
program-level reporting.

| ID | User Story | Priority |
|---|---|---|
| **US-4.1** | As **Dr. Anjali Rao**, I want to see my own session's test history and accuracy indicators, so that I can build trust in the tool over repeated use. | Must-have |
| **US-4.2** | As **Dr. Kavitha Menon (NPCB program officer)**, I want aggregate, de-identified statistics across all facilities using the tool, so that I can assess its impact without accessing any individual patient's data. | Should-have |
| **US-4.3** | As **Dr. Kavitha Menon**, I want to see the sanity-check pass rate across all logged cases, so that I can monitor whether the tool is functioning reliably at scale. | Should-have |

**Acceptance Criteria — US-4.1**
```gherkin
Given a surgeon has run one or more detections in their current session
When they navigate to the Insights tab
Then they see: count of tests run this session, average detected angle
  magnitude, and session-level sanity-check pass rate
And this data is scoped to the current browser session only — no
  cross-session or cross-user history is shown here
```

> **Extended by Epic 7 (Patient Profile):** the session-only scope above is
> unchanged for any test run without a patient profile attached. For a test
> that **is** attached to a patient (Epic 7), persistent, cross-session
> history is now available via that patient's profile view (US-7.3) — this
> is a genuinely different, additive surface, not a replacement for the
> lightweight session history described here.

**Acceptance Criteria — US-4.2 / US-4.3**
```gherkin
Given aggregate results exist in the backing data store
When an authorized stakeholder views the "All-time" insights panel
Then they see: total tests logged, average absolute angle across all tests,
  and overall sanity-check pass rate
And no field in this view contains patient names, hospital-assigned patient
  IDs, or any other patient-identifiable data — only aggregate numeric
  statistics and anonymized landmark descriptions
```

---

### Epic 5 — Data Privacy, Residency & Compliance Enforcement

**Epic Goal:** Ensure the product's data handling matches the DPDP Act commitments
established in Section 1 — as an enforced architectural constraint, not a policy
statement alone. **Updated by Epic 7:** this Epic originally enforced a
zero-patient-identifiable-data architecture; Epic 7 now stores real patient
identity by design, so this Epic's job shifts from "ensure no PII exists" to
"ensure the PII that now exists is consented to, access-controlled, and
erasable" — the same rigor, applied to a changed premise, not a lowered bar.

| ID | User Story | Priority |
|---|---|---|
| **US-5.1** | As the **system**, I want to process uploaded eye images transiently and discard the original image files after computing the result, so that no patient-identifiable photograph is ever persisted. | Must-have |
| **US-5.2** | As the **system**, I want all persisted data (logs, results) to reside exclusively within an India-based cloud region, so that data residency commitments are enforced at the infrastructure level, not just by policy. | Must-have |
| **US-5.3** | As **Mr. Suresh Iyer**, I want a plain-language data handling summary I can show hospital administration and auditors, so that approval doesn't stall on unclear data practices. | Should-have |
| **US-5.4** | *(Added by Epic 7)* As the **system**, I want a documented, testable right-to-erasure that cascades from a patient identity down through every case and result linked to them, so that DPDP Act erasure requests are honored completely, not just at the level of an isolated `test_id` row. | Must-have |

**Acceptance Criteria — US-5.1**
```gherkin
Given an eye image has been uploaded for detection
When the detection pipeline completes (success or failure)
Then the original image file is deleted from any temporary storage
  immediately after processing
And no image file, in any form, is written to persistent storage at any point
  in the pipeline
```

**Acceptance Criteria — US-5.2**
```gherkin
Given the system logs a completed detection result
When the record is written to the backing database
Then the database instance is provisioned in an India-based cloud region
  (e.g. asia-south1 or asia-south2)
And no data replication or backup target outside an India-based region is
  configured
```

**Acceptance Criteria — US-5.4:** defined in full under Epic 7 (US-7.5,
Section 2 below) rather than duplicated here, since cascading erasure depends
directly on the Patient Profile data model that Epic 7 introduces and this
Epic does not.

---

### Epic 6 — Case Reference & Toric Alignment Context

**Epic Goal:** Give each detection result meaningful clinical context — which
eye, which planned lens, and what the actual target axis is. **Historical
note, updated by Epic 7:** this Epic was originally designed to do so
*without ever storing real patient identity* — the pseudonymous Case
Reference was, at the time, the only way to add clinical context without
crossing the project's then-zero-PII line. Epic 7 has since crossed that
line deliberately. The two models are complementary, not competing: a Case
now typically belongs to a Patient Profile (Epic 7) via a `patient_id`
foreign key, while everything this Epic defines — eye laterality, target
axis, IOL model, the pattern-check safety nudge (US-6.4) — remains exactly
as useful and unchanged underneath that patient link.

**Why this Epic exists:** without it, a detection result is an orphaned
number — "9.8° rotation" with no link to a specific surgical case, and no way
to compute a real corrected target axis (the prototype currently uses a
hardcoded placeholder target of 85° for demo purposes, which has no clinical
meaning). This Epic closes that gap using a **pseudonymous Case Reference**
— a code the facility itself generates and controls (e.g., their own
internal case number) — rather than a patient identity record.

**Explicit boundary — what this Epic does NOT do (as originally scoped):**
on its own, a Case Reference does not store patient name, date of birth,
medical record number, or any other patient-identifying field — the code
itself is meaningless outside the facility's own separate patient chart.
**Updated by Epic 7:** a Case may now optionally (in practice, typically)
link to a full Patient Profile via `patient_id`. Where it does, the identity
lives on the Patient record (Epic 7), not on the Case itself — this Epic's
own fields (`case_ref`, `eye_laterality`, `target_axis_deg`, `iol_model`)
remain exactly as non-identifying as before; the identity boundary moved to
a different table, it wasn't erased from this one.

| ID | User Story | Priority |
|---|---|---|
| **US-6.1** | As **Dr. Anjali Rao (surgeon)**, I want to attach a case reference code (my own hospital's case number) to a detection, so that I can later match the result back to the correct surgical case in my own records. | Should-have |
| **US-6.2** | As **Dr. Anjali Rao**, I want to record the actual planned target axis for a case (from the patient's own pre-op biometry), so that the "corrected axis" the tool shows me is clinically real, not a placeholder. | Should-have |
| **US-6.3** | As **Dr. Anjali Rao**, I want to record which eye (left/right) a case refers to, so that results for a patient's two eyes are never confused with each other. | Should-have |
| **US-6.4** | As the **system**, I want to reject any case reference field that looks like it might contain a real name or date of birth, so that a well-meaning but risky entry (e.g. a surgeon typing a patient's actual name into the case field) doesn't accidentally reintroduce PII. | Must-have |

**Acceptance Criteria — US-6.1 / US-6.2 / US-6.3**
```gherkin
Given a surgeon is on the Analyze screen
When they optionally enter a case reference code, select an eye (OD/OS),
  and optionally enter a target axis in degrees
Then this information is attached to the detection result if a detection
  is run
And the "corrected axis" calculation uses the entered target axis instead
  of any hardcoded placeholder value, when one is provided
And all three fields remain fully optional - the core detection workflow
  (Epic 1-3) continues to function identically with no case reference
  attached, exactly as it does today
```

**Acceptance Criteria — US-6.4**
```gherkin
Given a surgeon enters a case reference code
When the value is submitted
Then the system performs a lightweight pattern check (e.g. flags values
  that look like a full name with a space between two capitalized words,
  or a date-like pattern resembling a date of birth)
And if flagged, the UI shows a warning: "This looks like it might contain
  a name or date of birth - please use your facility's case number
  instead" and asks for confirmation before proceeding
And this check is a safety nudge, not a hard technical guarantee - the
  facility's own data handling policy (Section 3.4 onboarding) remains the
  primary control, consistent with how Epic 5 already treats architectural
  enforcement as the primary safeguard with policy as a second layer
```

---

### Epic 7 — Patient Profile & Identified Clinical Records

**Epic Goal:** Give every detection result a real, persistent link to the
patient it was performed on — not just an orphaned angle number, and not
just a facility-controlled pseudonymous code (Epic 6) — so the tool can
function as a genuine part of that patient's surgical record: searchable by
name or MRN, viewable across both eyes and multiple visits, and auditable
back to a real identity when a facility's own clinical governance requires
it.

**Why this Epic exists, and what it changes:** Sections 1.5–1.7 and Epic 5
of this PRD were originally built around a **zero-patient-identifiable-data**
architecture — a deliberate choice to minimize DPDP Act and CDSCO compliance
surface area during the earliest prototype phase. Epic 6's pseudonymous Case
Reference was a first step toward real clinical context without crossing
that line. **This Epic deliberately crosses it**, based on direct product
direction that a tool with no patient record has limited real-world clinical
utility — a surgeon needs to find "Mrs. Lakshmi's left-eye case from
Tuesday," not hunt through a separate paper register for a matching case
code. Every section of this PRD that asserted the old zero-PII position is
updated alongside this Epic (see the "Updated by Epic 7" notes in Sections
1.5, 1.6, 1.7, 5.3, 5.5, Epic 5, the Risk Register, and Section 8.5) rather
than left standing in silent contradiction.

**Data relationship to toric IOL alignment analysis:**

```
Patient (1) ──< Case (many) ──< Result (many)
  name, DOB, MRN,      eye (OD/OS),          angle_deg, landmarks,
  facility_id           target_axis_deg,      passed_sanity_check,
                         iol_model             timestamp
```

- **One Patient** can have **multiple Cases** — typically one per eye (a
  patient needing bilateral toric IOLs has two Case records, one OD and one
  OS), or an additional Case if the same eye is revisited in a later
  surgical episode.
- **One Case** can have **multiple Results** — every detection attempt
  (including retries for a blurry photo) is preserved, so the surgeon's
  final decision is traceable back through every attempt made for that
  specific eye/surgery, not just the last one.
- This directly extends the `cases` table introduced in Epic 6 (which
  already carries `eye_laterality`, `target_axis_deg`, `iol_model`) by
  adding a required `patient_id` foreign key. Epic 6's case-level clinical
  context and this Epic's patient identity are complementary layers of the
  same relationship, not competing models — see the TDD's updated schema
  (Sections 3.2 and 3.2a) for the concrete column-level definition.

| ID | User Story | Priority |
|---|---|---|
| **US-7.1** | As **Dr. Anjali Rao (surgeon)**, I want to create a patient profile with their name, date of birth, and hospital MRN before running a test, so that the result is attached to the correct, real patient record rather than an anonymous code I have to cross-reference by hand. | Must-have |
| **US-7.2** | As **Dr. Anjali Rao**, I want to search for an existing patient by name or MRN before creating a new profile, so that a returning patient's second eye or a follow-up visit doesn't fragment into a duplicate, disconnected record. | Must-have |
| **US-7.3** | As **Dr. Anjali Rao**, I want to view a patient's full case and result history — both eyes, every visit — in one place, so that I have complete context before this test, not just whatever happened in my current browser session. | Must-have |
| **US-7.4** | As **Dr. Anjali Rao**, I want to record the patient's explicit consent for their identifiable data to be stored and processed, at the moment I create their profile, so that this use of their personal data is lawful under the DPDP Act rather than assumed. | Must-have |
| **US-7.5** | As **Mr. Suresh Iyer (procurement/compliance)**, I want a patient — and every case and result linked to them — to be fully and irreversibly deletable on request, so that the DPDP Act's right-to-erasure is honored at the level patients actually exercise it: as a person, not as an isolated `test_id`. | Must-have |
| **US-7.6** | As the **system**, I want patient profile access restricted to authenticated staff at the patient's own facility, so that a clinician at Hospital A can never search, view, or export a patient record belonging to Hospital B. | Must-have |
| **US-7.7** | As **Dr. Kavitha Menon (NPCB program officer)**, I want the Insights/aggregate view (Epic 4) to remain fully de-identified regardless of Epic 7, so that program-level reporting never surfaces a patient name or MRN even though the underlying records now contain them. | Must-have |

**Acceptance Criteria — US-7.1 / US-7.2**
```gherkin
Given a surgeon is starting a new case on the Analyze screen
When they search by patient name or MRN and no match is found
Then they can create a new patient profile with required fields: full name,
  date of birth, and facility MRN (a phone number is optional)
And the profile is stored with the creating clinician's Firebase UID and
  facility_id, and a system-generated patient_id (UUID) is returned

Given a surgeon searches by an existing patient's name or MRN
When one or more matches are found within their own facility
Then the matching profile(s) are shown for selection, scoped strictly to
  the searching clinician's own facility_id (US-7.6)
And selecting one attaches the new case to that existing patient rather
  than creating a duplicate profile
```

**Acceptance Criteria — US-7.3**
```gherkin
Given a patient profile has one or more cases and results attached
When a surgeon with facility access opens that patient's profile
Then they see every case (with eye laterality and target axis) and every
  result under it (angle, sanity flags, timestamp), ordered most-recent-first
And this view is not limited to the current browser session, unlike Epic
  4's original History screen (US-4.1), which remains available unchanged
  for any test that has no patient profile attached
```

**Acceptance Criteria — US-7.4**
```gherkin
Given a surgeon is creating a new patient profile
When they reach the final confirmation step
Then they must explicitly check a consent confirmation ("Patient has been
  informed their name, DOB, and MRN will be stored for this test and
  linked to their surgical record") before the profile can be saved
And the profile is not created if this box is left unchecked
And the consent event itself (who captured it, when) is recorded on the
  patient record, distinct from and in addition to the DPDP Act's general
  lawful-processing basis
```

**Acceptance Criteria — US-7.5**
```gherkin
Given a patient profile exists, with or without linked cases/results
When an authorized administrator issues a deletion request for that
  patient_id
Then the patient profile and every case and result row linked to it (via
  patient_id -> case_ref -> test_id) are permanently deleted in a single
  cascading operation
And this deletion is irreversible and is logged (who requested it, when)
  in a separate audit record that itself contains no patient-identifiable
  fields — only the fact that a deletion occurred
```

**Acceptance Criteria — US-7.6**
```gherkin
Given a clinician is authenticated with a facility_id claim (TDD Section 5.2)
When they search for or attempt to open a patient profile
Then the system returns only profiles whose facility_id matches their own
And a direct request for a patient_id belonging to a different facility_id
  is rejected with the same generic not-found response used for a
  nonexistent patient_id, so a mismatched facility can't even confirm the
  patient_id exists elsewhere
```

**Acceptance Criteria — US-7.7**
```gherkin
Given the /stats aggregate endpoint (Epic 4) is queried
When the response is constructed
Then it draws only from the results/cases aggregate fields already
  permitted under US-4.2/US-4.3 (counts, average angle, pass rate)
And no query path exists from /stats into the patients table — aggregate
  reporting and identified patient records are architecturally separate
  read paths, not merely filtered views of the same one
```

---

### Epic Prioritization Summary

| Epic | MoSCoW Priority | Rationale |
|---|---|---|
| 1. Image Capture | Must-have | No workflow exists without it |
| 2. AI Detection & Calculation | Must-have | Core value proposition |
| 3. Safety & Validation | Must-have | Non-negotiable given clinical context |
| 5. Privacy & Compliance | Must-have | Legal/ethical floor, not optional |
| 4. Insights & Analytics | Should-have | High value, but product functions without it in a pinch |
| 6. Case Reference & Alignment Context | Should-have | Adds real clinical utility, but the core detection workflow is fully functional without it |
| 7. Patient Profile & Identified Clinical Records | Must-have | Without it, per direct product direction, results carry no real-world clinical utility — a hospital record must be findable by patient, not just by an internal case code |

---

*End of Section 2.*

---

## Section 3: Government Adoption & Distribution Model

Since CyclotorsionCheck is a non-profit, portfolio-driven initiative rather than a
commercial product, this section replaces a traditional pricing/GTM strategy with
an **adoption and sustainability model** — how a free tool actually gets into a
government hospital's hands, and who bears the (non-zero) cost of running it.

---

### 3.1 Adoption Model Overview

The product is **free to the hospital at point of use**. This does not mean it is
free to operate — Cloud Run, Gemini API calls, and BigQuery storage carry real
(if modest) costs. The adoption model must therefore answer two separate
questions: *how does a hospital start using this*, and *who pays for the
infrastructure it runs on*.

| Question | Answer |
|---|---|
| Who pays for cloud infrastructure? | Sponsored via Google for Nonprofits / Google Cloud for Startups-equivalent credit programs, or a CSR/grant partner, at least through the pilot phase |
| Who pays for the software itself? | No one — source-available, non-commercial |
| Who owns the deployment? | The sponsoring/portfolio organization operates a shared instance during pilot; a per-state-health-department instance is a Phase 2 consideration (Section 8.2) |

---

### 3.2 Distribution Channels

| Channel | Description | Priority |
|---|---|---|
| **Direct MoU with State Health Department / District Health Society** | Formal (non-financial) Memorandum of Understanding authorizing pilot use at named facilities, routed through the existing NPCB program structure rather than a separate approval track | Primary channel for V1 |
| **GeM (Government e-Marketplace) listing** | Even zero-cost software can be listed as a "free" catalog item on GeM, which lowers institutional friction for hospitals used to procuring exclusively through this channel | Secondary — pursued once pilot evidence exists |
| **Professional body endorsement** | Engagement with bodies such as the All India Ophthalmological Society (AIOS) for visibility and clinical credibility among practicing surgeons | Secondary |
| **NGO / CSR partnership** | Partnering with an existing eye-care NGO (e.g., organizations already running high-volume government-linked cataract programs) to piggyback distribution on an existing trusted relationship, rather than cold-approaching hospitals | Preferred entry path for V1 pilot sites |

> **Design rationale:** Cold outreach to individual government hospitals is slow
> and trust-limited. Routing through an existing NGO/CSR eye-care partner with
> established government relationships is the realistic path to the 1–3 pilot
> sites targeted in Section 1.6.

---

### 3.3 Funding & Sustainability Model

| Cost Category | Estimated Driver | Funding Source (V1) |
|---|---|---|
| Gemini API calls | ~2 calls per patient case (landmark detection ×2) | Free-tier where possible; paid tier covered by sponsor credits once free-tier limits are exceeded |
| Cloud Run hosting | Scales to zero when idle; near-negligible at pilot volume | Sponsor cloud credits |
| BigQuery storage/queries | Minimal at pilot scale (structured numeric rows, no images) | Sponsor cloud credits |
| Firebase Hosting | Free tier sufficient at pilot scale | Free tier |

**Sustainability risk (see also Section 7, Risk Register):** the current model
depends on donated/sponsored cloud credits. If the project scales beyond pilot
volume without a committed funding partner, cost coverage becomes the single
largest threat to continuity — this is treated as an open risk, not a solved
problem, at this stage of the PRD.

---

### 3.4 Hospital Onboarding Workflow (V1)

```gherkin
Given a facility has been approved via MoU or NGO-partner referral
When onboarding begins
Then a facility administrator account is created (no patient data involved)
And at least one surgeon account is created and provided a short (<15 minute)
  orientation covering: capture technique, reading flagged results, and the
  tool's non-diagnostic status
And a designated point of contact is recorded for issue escalation
```

No hospital-side infrastructure, EHR access, or IT integration is required for
onboarding — a deliberate design constraint to keep adoption friction near zero
(consistent with Section 1.7's exclusion of EHR/ABDM integration from V1).

---

*End of Section 3.*

---

## Section 4: Clinical Validation Pathway

The Definition of Done established in our scoping (Section 1.5/1.6) requires
matching or exceeding the synthetic-data benchmark (~0.14° MAE) **on real clinical
images**. This section defines how that evidence gets produced ethically and
credibly — the single biggest gap between "working prototype" and "defensible
clinical tool."

---

### 4.1 The Core Validation Challenge: Establishing Ground Truth

Unlike the synthetic dataset — where the true rotation angle is known exactly
because we generated it — **real patients have no known ground-truth cyclotorsion
angle**. This must be established independently before the AI's output can be
scored against it.

**Proposed ground-truth method for V1 validation:**

| Approach | Description | Trade-off |
|---|---|---|
| **A — Reference marker comparison** | Compare CyclotorsionCheck's output against a manually-placed reference mark measured by a masked second observer (e.g., ophthalmology resident) using standard goniometry, on the same patient | Introduces observer variability as a confound; still the most practical option without expensive comparator hardware |
| **B — Comparator device** | Compare against an existing digital marker system (VERION/CALLISTO) at a partner facility that has one | Higher-confidence ground truth, but only feasible if a partner site with such hardware agrees to co-validation — reduces accessible pilot sites |
| **C — Synthetic-to-real transfer only (no live ground truth)** | Validate landmark-detection reliability qualitatively on real images without a numeric ground-truth angle | Insufficient to satisfy the stated Definition of Done; listed only for completeness, not recommended |

**Recommendation:** Pursue **Approach A** as the primary V1 method (accessible at
any pilot site), with **Approach B** opportunistically layered in if a comparator
device becomes available at any partner facility — giving a stronger secondary
validation point without blocking on it.

---

### 4.2 Institutional & Ethical Approval Process

```gherkin
Given real patient eye photographs will be captured for validation purposes
When the study is initiated at a pilot site
Then Institutional Ethics Committee (IEC) approval is obtained from that
  facility (or its affiliated medical college, where the district hospital
  itself has no standing IEC) prior to any image capture
And the study protocol is designed in accordance with the ICMR National
  Ethical Guidelines for Biomedical and Health Research Involving Human
  Participants (2017, as amended)
And informed patient consent is obtained in the patient's preferred language,
  explicitly covering: purpose of image capture, non-diagnostic/research use,
  data retention policy (see Section 5, US-5.1), and voluntary participation
  with no impact on standard-of-care treatment if declined
```

**Distinct from Epic 7's routine-care consent:** the informed consent above
governs *research/validation use* of a patient's image and IEC-approved study
protocol. It is separate from, and in addition to, the DPDP-basis consent
Epic 7 (US-7.4) captures for the routine clinical use of a patient's identity
data in ordinary (non-research) tool operation — a facility running the tool
purely for its own patients' clinical care, outside this validation study,
still needs US-7.4's consent, but does not need IEC approval for that
routine use.

**Sequencing dependency:** IEC approval is a hard gate — no real patient image may
be captured for validation purposes before it is granted. This is called out
explicitly because it is the most common point where hackathon/portfolio AI health
projects either stall or (worse) skip the step improperly.

---

### 4.3 Study Design

| Parameter | V1 Specification |
|---|---|
| **Study type** | Prospective, single-arm accuracy validation (not a randomized clinical trial) |
| **Primary endpoint** | Mean Absolute Error (MAE) between CyclotorsionCheck's calculated angle and the reference ground-truth angle (Section 4.1) |
| **Target sample size** | Minimum 30 patient cases for an initial pilot-scale accuracy signal; formally powered sample size to be calculated with a biostatistician before any claim intended for CDSCO submission |
| **Inclusion criteria** | Adult patients undergoing cataract surgery with pre-identified astigmatism requiring toric IOL, able to provide informed consent |
| **Exclusion criteria** | Corneal pathology or scarring that would obscure landmark identification; inability to provide consent |
| **Success criterion** | MAE on real images ≤ MAE on synthetic benchmark (0.14°), or a pre-agreed acceptable margin above it (e.g. ≤1.0°) if real-world imaging conditions (glare, motion) introduce expected additional noise |

> **Honesty note carried over from the prototype's own documentation:** real
> clinical images will almost certainly be noisier than synthetic ones (glare,
> motion, corneal reflections). Setting the bar at "≤1.0° acceptable margin" rather
> than requiring the synthetic result to be matched exactly is a deliberately
> realistic target, not a lowering of ambition — an inflated claim here would
> undermine the tool's credibility with exactly the clinical/regulatory audience
> it needs to convince.

---

### 4.4 Pathway to CDSCO Alignment

This validation is scoped to support the **Class A / non-diagnostic** positioning
established in Section 1.5 — it is evidentiary support for continued safe
operation as a decision-support aid, not a submission-ready CDSCO Class B+ dossier.
Escalation to a formal CDSCO submission, should the project pursue that path, is
addressed in Section 8 (Phased Roadmap) and would require a materially larger,
externally-powered study beyond V1 scope.

---

*End of Section 4.*

---

## Section 5: Data Residency & Cloud Region Specificity

Section 1.5 commits to data remaining within India, and Epic 5 (US-5.2) makes this
an enforced architectural constraint. This section specifies exactly how that
commitment is implemented — the concrete mechanism, not just the policy.

---

### 5.1 Target Cloud Region

| Service | Target Region | Rationale |
|---|---|---|
| Cloud Run (detection API) | `asia-south1` (Mumbai) | Primary recommendation — lowest latency for pilot sites in southern/western India where early NGO eye-care partnerships are concentrated |
| BigQuery (results storage) | `asia-south1` | Co-located with Cloud Run to avoid cross-region data transfer |
| Firebase Hosting | Firebase Hosting is served via global CDN by default; the **static frontend assets** (HTML/CSS/JS) contain no patient data, so CDN edge caching is acceptable — only backend/database services carry the residency constraint |
| Cloud Storage (if introduced later for any interim file handling) | `asia-south1`, with a lifecycle policy enforcing auto-deletion (see Section 5.3) | No such storage exists in the current architecture — see Section 5.2 |

**Alternate region:** `asia-south2` (Delhi) is an acceptable substitute if a
specific pilot partnership makes it operationally preferable (e.g., NGO or health
department infrastructure concentrated in northern India). The requirement is
"an India-based Google Cloud region," not a specific one.

---

### 5.2 ⚠️ Current Implementation Gap

> **This is flagged here deliberately, not glossed over.** The working prototype
> built during initial development is currently deployed in `us-central1` (Iowa,
> USA) — a direct contradiction of the data residency commitment in Section 1.5.
>
> **Decision (recorded here for traceability):** `us-central1` is accepted as-is
> for the demo/portfolio phase, since only synthetic, non-patient data has ever
> been processed there. This is a deliberate, documented exception — not an
> oversight — scoped strictly to the demo phase. **Migration to an India-based
> region remains a hard blocking requirement before any real patient image is
> processed**, including the Section 4 validation study. This gate does not move.

**Remediation action:** Re-deploy Cloud Run service and BigQuery dataset to
`asia-south1` prior to Section 4 validation kickoff. This is a redeploy-and-verify
operation (same process used throughout the prototype build), not a rearchitecture
— tracked as a pre-validation gate, not a future-phase item.

---

### 5.3 Data Lifecycle & Retention

Consistent with Epic 5 (US-5.1):

| Data Type | Retention | Deletion Mechanism |
|---|---|---|
| Uploaded eye photograph (original file) | **Zero persistence.** Processed in-memory/temp storage during the request only. | Deleted immediately after detection completes (already implemented in the prototype's request-handling logic — temp files are cleared after each detection call) |
| Calculated angle result (numeric) | Retained indefinitely for aggregate analytics (Epic 4), unless a facility requests deletion | Row-level deletion supported via `test_id` on request |
| Landmark text description (e.g. "small red spot on sclera") | Retained alongside the numeric result | Same as above — this is a generic anatomical description, not patient-identifiable |
| Session/login data (demo auth) | Client-side only (browser local storage) in current prototype — **not a real authentication system**, see Section 5.4 | Cleared by the user's own browser controls; nothing to delete server-side |
| **Patient profile (name, DOB, MRN, facility_id)** — *added by Epic 7* | Retained indefinitely as part of the patient's real hospital record, unless the patient (or facility on their behalf) exercises DPDP right-to-erasure | **Cascading** deletion via `patient_id` (Epic 7, US-7.5) — removes the patient profile and every case/result linked to it in one operation, not just a single row |
| **Consent record (who captured consent, when)** — *added by Epic 7* | Retained alongside the patient profile as evidence of lawful processing | Deleted along with the patient profile on erasure; the *fact* that a deletion occurred is separately audit-logged (US-7.5) without retaining any identifiable field in that log |

**Updated by Epic 7 — this is no longer accurate as originally written:**
~~No field in the persisted database schema is patient-identifiable~~ — the
`results`/`cases` tables themselves remain exactly as described (`test_id`,
`timestamp`, `angle_deg`, landmark descriptions, pass/fail flags — no
identity fields). **The new `patients` table (Epic 7, TDD Section 3.2) does
now store patient name, date of birth, and MRN by design.** The
zero-identifiability guarantee is preserved for the `results`/`cases`
tables specifically, and for eye images always — it is no longer a
whole-system guarantee.

---

### 5.4 ⚠️ Authentication Gap — Flagged for Resolution Before Pilot

> The prototype's current login/registration system is **explicitly a client-side
> demo only**, storing credentials in browser local storage with no real backend
> authentication, password hashing, or session security. This was appropriate for
> demo purposes but is **not acceptable for any deployment handling real patient
> workflows**, even with zero patient data stored, because facility-level access
> control (who can operate the tool at a given hospital) still needs to be real.

**Remediation action:** Before pilot deployment (Section 3.4 onboarding), replace
the demo auth with a real identity provider — Firebase Authentication (already
available given the existing Firebase Hosting setup) is the lowest-friction
option, requiring no new vendor relationship.

**Recommended fix — detailed:**

| Aspect | Recommendation |
|---|---|
| **Provider** | Firebase Authentication, Email/Password method. Zero new infrastructure — same Firebase project already hosting the frontend. |
| **Why not the current approach** | Client-side local-storage "auth" has no real password verification, no session expiry, and is trivially bypassed by editing browser storage — acceptable for a demo, not for controlling who can operate a clinical tool at a hospital |
| **Why not Google/OAuth sign-in** | Adds a dependency on staff having personal Google accounts tied to hospital use, which complicates facility-level account provisioning and offboarding — Email/Password under Firebase's own management is simpler for an institutional rollout |
| **Effort estimate** | Low — 1 of the two demo screens (login) needs its handlers rewritten to call the Firebase Auth SDK instead of `localStorage`; registration UI stays visually identical, only the backing calls change |
| **Migration steps** | 1. Enable Email/Password sign-in in Firebase Console → Authentication → Sign-in method<br>2. Replace `handleLogin()`/`handleRegister()` JS functions to call `signInWithEmailAndPassword()` / `createUserWithEmailAndPassword()` from the Firebase JS SDK<br>3. Replace the `cc_demo_session` local-storage session check with Firebase's `onAuthStateChanged()` listener<br>4. Add facility-level role/claims (e.g. custom claims marking a user as belonging to a specific hospital) so Epic 4's aggregate analytics can eventually be facility-scoped if needed |
| **What this does NOT yet solve** | Firebase Auth alone doesn't add hospital-level *admin approval* of new accounts (i.e. self-service registration would let anyone sign up). For pilot phase, pair this with a simple allow-list (e.g. Mr. Suresh Iyer manually provisions accounts) rather than open self-registration — closes the gap without needing a full admin console build |

---

### 5.5 Encryption & Access Control

| Layer | Mechanism |
|---|---|
| Data in transit | HTTPS/TLS enforced by default on Cloud Run and Firebase Hosting |
| Data at rest | Google Cloud default encryption at rest (BigQuery, Cloud Run) for the `results`/`cases` tables and all non-patient data. **Updated by Epic 7:** the new `patients` table stores real identity fields (name, DOB, MRN) — customer-managed encryption keys (CMEK) are now **recommended** for that table specifically, reversing the original "not required" stance, which was explicitly justified by data being non-identifiable. That justification no longer holds for `patients`. |
| API access | Cloud Run endpoint remains `--allow-unauthenticated` at the infrastructure level (appropriate for a public multi-user browser app), but `/detect` and `/stats` are now protected by **application-layer Firebase ID token verification** — implemented and resolved, not merely recommended. `/health` remains open for uptime monitoring. **Updated by Epic 7:** patient-profile endpoints additionally enforce facility-scoped authorization (US-7.6), not just authentication — a valid token alone is not sufficient to read a patient record outside the caller's own facility. |

**Status: Resolved, with one item reopened by Epic 7.** The authentication
gap (5.4) and the open API access gap noted above have been implemented in
the current codebase — Firebase Authentication replaces the demo
local-storage login, and both sensitive endpoints now verify the caller's
Firebase ID token server-side before processing a request. The data
residency region migration (5.2) remains an open, deliberately deferred item
pending pilot kickoff. **CMEK for the `patients` table (above) is a new,
currently-open item introduced by Epic 7** — tracked as a pre-pilot
requirement alongside 5.2, not yet implemented as of this PRD update.

---

*End of Section 5.*

---

## Section 6: Low-Connectivity / Offline Resilience

The current architecture (Cloud Run + Gemini API + BigQuery, all called live from
the browser) requires a stable internet connection for every single detection.
This is a real and material adoption risk for government hospitals outside major
metros, where connectivity is often intermittent.

---

### 6.1 Current State (V1)

**V1 is cloud-only, with no offline fallback.** This is an explicit, accepted
limitation for the pilot phase (consistent with Section 1.7's original scope
exclusion), not an oversight — building genuine offline inference is a
significant engineering lift (on-device model, local landmark detection without
Gemini) that would meaningfully delay the pilot timeline for a capability most
pilot sites may not strictly need on day one.

---

### 6.2 Risk Assessment

| Scenario | Impact | Likelihood at Pilot Sites |
|---|---|---|
| Brief connectivity drop during use | Detection request fails or times out; surgeon retries once connection returns | Moderate — common in tier-2/3 city hospitals |
| Sustained outage during a surgical session | Tool is fully unusable; surgeon must revert to ink-marker fallback | Low-moderate, but high-severity when it occurs |
| No internet access at facility at all | Facility cannot be onboarded under current architecture | Site-selection filter — see 6.3 |

---

### 6.3 V1 Mitigation (Without Building Offline Mode)

Rather than building offline capability into V1, risk is mitigated through
**pilot site selection and graceful degradation**:

```gherkin
Given a pilot facility is being evaluated for onboarding (Section 3.4)
When connectivity is assessed as part of site selection
Then only facilities with a documented, reasonably reliable internet
  connection (e.g. existing hospital broadband/leased line, not solely
  mobile data) are selected for V1 piloting
And this requirement is disclosed transparently as a current-version
  limitation, not hidden from prospective pilot partners
```

```gherkin
Given a detection request fails due to network/connectivity issues
When the frontend receives a network error (not a server error)
Then the user sees a clear, specific message: "Connection issue — check your
  internet and try again" (distinct from the generic error message), so the
  surgeon isn't left guessing whether the tool or the network failed
And the tool never silently hangs — a timeout (e.g. 30 seconds) triggers this
  message rather than leaving the "Analyzing..." state indefinitely
```

**Fallback expectation:** The tool is explicitly designed to be **additive** to
existing practice, not a replacement dependency. If unavailable at the moment of
need, the surgeon reverts to standard ink-marker technique — the same practice
used before the tool existed. This is stated explicitly in surgeon onboarding
(Section 3.4) so the tool is never a single point of failure for the surgery
itself.

---

### 6.4 Future Consideration (Phase 2+, Not V1)

Deferred to the Phased Roadmap (Section 8.4): an on-device or edge-deployed
lightweight landmark-detection model (e.g., a distilled vision model run via
TensorFlow Lite or similar) that could operate without a live Gemini API call,
falling back to cloud-based detection only when available for higher accuracy.
This is explicitly **not** committed to for V1 — flagged here only so it isn't
lost as a known future direction.

---

*End of Section 6.*

---

## Section 7: Risk Register

Consolidating every risk surfaced across Sections 1–6 into a single view, scored
for stakeholder review. This is deliberately comprehensive rather than
selectively optimistic — a risk register that only lists solved problems isn't
useful to anyone evaluating the project.

| # | Risk | Category | Likelihood | Impact | Status | Mitigation |
|---|---|---|---|---|---|---|
| R1 | Data residency: current deployment in `us-central1`, not India | Compliance | Certain (current state) | High if unresolved before real patient data is used | **Open — accepted for demo phase only** | Hard gate before Section 4 validation; migrate to `asia-south1` (Section 5.2) |
| R2 | Client-side demo authentication had no real access control | Security | Was certain (current state, pre-fix) | High | **Resolved** | Replaced with Firebase Authentication + server-side token verification (Sections 5.4, 6-implementation) |
| R3 | API endpoints were fully public/unauthenticated | Security | Was certain (current state, pre-fix) | Medium-High | **Resolved** | Firebase ID token verification added to `/detect` and `/stats` |
| R4 | No offline/low-connectivity fallback | Product/Adoption | Moderate at some pilot sites | Medium | **Open — accepted, mitigated via site selection** | Pilot site connectivity screening (Section 6.3); graceful error messaging; genuine offline mode deferred to Phase 2+ |
| R5 | Real-world clinical image accuracy may not match synthetic benchmark (0.14° MAE) | Clinical/Product | Likely to some degree | Medium-High | **Open — anticipated, not yet measured** | Realistic acceptance margin (≤1.0°) set in Section 4.3; validation study designed to measure, not assume |
| R6 | No formal ground truth exists for real patient cyclotorsion angle | Clinical/Methodology | Certain (inherent to the problem) | Medium | **Open — addressed via study design** | Masked second-observer reference marking (Approach A, Section 4.1) |
| R7 | Cloud infrastructure cost sustainability beyond pilot | Financial/Sustainability | Moderate-High if scaling without funding partner | High at scale | **Open — explicitly unsolved** | Sponsor/CSR credit model for V1 (Section 3.3); no committed long-term funding source yet |
| R8 | Single-AI-provider dependency (Google Gemini) | Technical | Low-Moderate | Medium | **Open — accepted** | No mitigation planned for V1; noted as an architectural dependency, not actively de-risked |
| R9 | Government procurement/MoU approval cycles can span 12–18+ months | Adoption/Timeline | High (typical for public-sector engagement) | Medium (delays timeline, doesn't block feasibility) | **Open — inherent to channel** | NGO/CSR-partner distribution channel chosen specifically to reduce (not eliminate) this delay (Section 3.2) |
| R10 | IEC/ethics approval could be denied or significantly delayed at a given pilot site | Regulatory/Clinical | Moderate | High (blocks that specific site) | **Open — inherent to process** | Multiple candidate pilot sites recommended rather than single-site dependency |
| R11 | Facility self-registration without admin approval could allow unauthorized accounts | Security | Moderate (until addressed) | **Updated by Epic 7 — Medium-High** (an unauthorized account can now reach real patient identity data, not just aggregate numbers) | **Open — documented workaround only** | Manual allow-listing by facility procurement lead (Section 5.4) pending a proper admin-approval flow |
| R12 | *(Added by Epic 7)* Storing real patient identity (name, DOB, MRN) materially raises the DPDP Act compliance burden — consent capture, breach notification, and right-to-erasure are now live legal obligations, not moot architectural non-applicability | Compliance/Legal | Certain (inherent to the Epic 7 decision) | High if consent/erasure/breach-notification processes are incomplete at pilot launch | **Open — newly introduced, must close before pilot** | Consent capture (US-7.4) and cascading erasure (US-7.5) are Must-have V1 requirements, not deferred; CMEK adoption (Section 5.5) tracked as a companion pre-pilot item |
| R13 | *(Added by Epic 7)* A facility-scoping bug in patient-profile access (US-7.6) would expose one hospital's real patient records to another hospital's staff — a materially more severe failure mode than any pre-Epic-7 data exposure, since the underlying data is now identifiable | Security | Low (with US-7.6 correctly implemented and tested) | Very High if it occurs | **Open — mitigated by design, not yet independently verified** | Facility-scoped authorization at the API layer (US-7.6), same-generic-404 pattern to prevent facility-existence probing; recommend a dedicated cross-facility access test in the pilot go-live checklist |

---

### 7.1 Risk Summary by Category

| Category | Open Risks | Resolved Risks |
|---|---|---|
| Compliance / Data Residency | 1 (R1) | 0 |
| Compliance / Legal (Patient Data) | 1 (R12) | 0 |
| Security | 2 (R11, R13) | 2 (R2, R3) |
| Clinical / Methodology | 2 (R5, R6) | 0 |
| Product / Adoption | 1 (R4) | 0 |
| Financial / Sustainability | 1 (R7) | 0 |
| Technical | 1 (R8) | 0 |
| Regulatory / Timeline | 2 (R9, R10) | 0 |

**Honest read of this table:** two genuine security gaps have been closed during
this PRD process (R2, R3) — showing the document actively drove real fixes, not
just paperwork. The remaining open risks are largely **structural to operating a
non-profit health-tech pilot in the Indian public sector** (funding, procurement
timelines, ethics approval) rather than engineering shortcomings, and are
appropriately owned by the project's adoption and validation strategy (Sections
3–4) rather than by further product development.

---

*End of Section 7.*

---

## Section 8: Phased Roadmap Beyond V1

V1 is intentionally narrow — a single-workflow pilot tool. This section lays out
what comes next, **gated by validation outcomes rather than calendar dates**,
which is the appropriate sequencing discipline for a non-profit pilot where
premature scope expansion is a bigger risk than being "too slow."

---

### 8.1 Phase Gating Principle

> No phase begins until its stated entry criterion is met. This section is a
> roadmap of *possibilities*, not a committed timeline — committing to Phase 3
> features before Phase 1 clinical validation completes would be premature and
> is explicitly avoided here.

---

### 8.2 Phase 2 — Validated Pilot Expansion

**Entry criterion:** Section 4 clinical validation completes with results meeting
the defined success criterion (MAE ≤1.0° on real images).

| Capability | Description |
|---|---|
| Multi-facility rollout | Expand from 1–3 pilot sites to a broader set within the same state health department relationship, reusing the Section 3 onboarding model |
| Facility-scoped analytics | Extend Epic 4 (Insights) so aggregate stats can be filtered by facility — requires the Firebase custom-claims groundwork already noted in Section 5.4's remediation plan |
| Proper admin-approval flow | Replace the manual allow-listing workaround (R11, Section 7) with a real facility-admin account-approval interface |
| Data residency migration executed | Close R1 (Section 7) — migrate to `asia-south1` as a precondition, not a parallel-track item |

---

### 8.3 Phase 3 — National Digital Health Alignment

**Entry criterion:** Phase 2 shows sustained multi-facility usage with stable
accuracy and no material safety flags requiring escalation.

| Capability | Description |
|---|---|
| ABDM alignment (exploratory) | Investigate whether logging de-identified, aggregate procedure-outcome data (not patient records) into ABDM-compatible reporting structures adds value to NPCB program reporting — **not** full patient-record EHR integration |
| FHIR-compatible export (optional) | If a specific partner facility requests it, provide an India-specific FHIR implementation guide-compatible export of aggregate statistics only — evaluated case-by-case, not built speculatively |
| GeM formal listing | Pursue formal GeM catalog listing (Section 3.2) once pilot evidence exists to support the listing application |

---

### 8.4 Phase 4 — Technical Depth (Conditional, Lower Priority)

These are **only** pursued if a specific, demonstrated need emerges — not built
proactively:

| Capability | Trigger Condition |
|---|---|
| Offline/edge inference (Section 6.4) | A specific pilot site's connectivity proves to be a recurring, material adoption blocker — not built speculatively ahead of that evidence |
| CDSCO Class B+ submission pathway | A funding partner or institutional sponsor commits to underwriting a properly-powered, externally-run clinical trial (Section 4.4) — this is a large undertaking not appropriate to self-fund as a portfolio project |
| Pupil-center auto-detection (replacing the current image-center assumption) | If validation data (Section 4) shows the current center-assumption introduces meaningful error, replacing it with true pupil-center detection becomes a priority; otherwise it remains a known, accepted simplification |

---

### 8.5 What Explicitly Does Not Change Across Phases

Regardless of which phase is reached, the following commitments from Section 1
remain fixed and are not "phased away" under growth pressure:

- Non-diagnostic, decision-support positioning (Section 1.5) — escalating to a
  higher CDSCO classification is a deliberate, evidence-gated choice (8.4), not
  a default trajectory
- **Updated by Epic 7:** ~~Zero patient-identifiable data retention~~ no
  longer applies system-wide (Epic 7 stores real patient identity by
  design). What remains fixed instead: **zero eye-image persistence**
  (unchanged, Section 5.3) and **100% consented, facility-scoped patient
  data** (the KPI Epic 7 introduced in Section 1.6) — the underlying
  commitment to not casually or unlawfully expose patient data survives;
  its specific mechanism (having none to expose) does not
- India-only data residency (Section 5.1)
- Free-to-hospital access model (Section 3.1) — commercialization is not a
  roadmap item at any phase, consistent with the project's non-profit framing

---

*End of Section 8.*

---

## Section 9: Alignment with Government Health Schemes

This section connects CyclotorsionCheck explicitly to existing national digital
health and public health infrastructure priorities — not because alignment is
required for the tool to function, but because **demonstrated fit with existing
government programs is a genuine, practical factor in public-sector adoption
decisions**, and because it clarifies that this project extends rather than
duplicates existing national efforts.

---

### 9.1 National Programme for Control of Blindness & Visual Impairment (NPCB&VI)

CyclotorsionCheck operates entirely within the existing cataract-surgery workflow
run under NPCB&VI at government facilities (referenced in Section 1.2's market
context). It introduces no new program structure, reporting hierarchy, or
patient pathway — it is a point-of-care tool used *within* an NPCB&VI procedure,
not a parallel system. This is a deliberate positioning choice: the tool aims to
improve outcome quality within the existing national program, not to establish
a competing initiative.

**Practical implication:** any future aggregate reporting (Section 8.3's
exploratory ABDM alignment) would be additive to existing NPCB&VI reporting
mechanisms, not a replacement for them.

---

### 9.2 Ayushman Bharat Digital Mission (ABDM)

ABDM's mission is to create a longitudinal digital health ecosystem for Indian
citizens through interoperable health records and federated architecture. V1 of
CyclotorsionCheck deliberately does **not** integrate with ABDM (Section 1.7, Out
of Scope) because:

- ABDM's Health Information Exchange is designed around patient-linked records
  — CyclotorsionCheck's architecture intentionally avoids retaining any
  patient-linked data at all (Section 5.3), so a naive integration would work
  against, not with, the current privacy design
- Premature EHR/ABDM integration would meaningfully increase compliance
  surface area (informed consent scope, data-sharing agreements) before the
  tool has even completed real-world clinical validation (Section 4)

**Where alignment is genuinely relevant:** Section 8.3 already scopes a
Phase 3, evidence-gated exploration of contributing **de-identified, aggregate**
outcome statistics (e.g., "facility X ran N verified toric alignments this
quarter with Y% within tolerance") into ABDM-compatible reporting — a
meaningfully different, much lower-risk proposition than patient-record
integration, and one that stays consistent with the zero-patient-data
architecture rather than compromising it.

---

### 9.3 Digital India

CyclotorsionCheck's core thesis — that widely available consumer-grade
technology (a smartphone camera, a free-tier cloud AI account) can substitute
for specialized, expensive medical hardware — is a direct expression of the
Digital India program's broader goal of using digital technology to improve
public service delivery and reduce cost barriers to essential services. This is
cited here as a values-alignment statement, not a claim of formal Digital India
program participation or endorsement.

---

### 9.4 Explicit Non-Claims

To avoid overstating alignment, this PRD makes no claim that CyclotorsionCheck
is formally endorsed, funded, or operated by NPCB&VI, ABDM, or the Digital India
program. All statements above describe **thematic and architectural
compatibility**, intended to inform pilot-partner conversations — not existing
institutional relationships. Any future formal engagement with these programs
(e.g., an actual ABDM sandbox integration) would be a distinct, separately
scoped undertaking beyond this PRD's current commitments.

---

*End of Section 9. This completes the PRD as scoped: the original 14 planned
content areas are all covered, organized into 9 top-level sections — the first
7 content areas (Executive Summary through Out of Scope) live as subsections
1.1–1.7 within Section 1, Epics & User Stories form Section 2, and the seven
additional areas we agreed to add (Government Adoption, Clinical Validation,
Data Residency, Offline Resilience, Risk Register, Phased Roadmap, and
Government Health Scheme Alignment) form Sections 3–9.*

---

## Document Status & Next Steps

This PRD is a **living document** appropriate for a portfolio/pilot-stage
project. Recommended next actions outside this document:

1. Execute the R1 remediation (Section 5.2) — migrate infrastructure to
   `asia-south1` — before any real patient data is introduced
2. Identify and formally engage the NGO/CSR pilot partner referenced in
   Section 3.2, to begin the Section 3.4 onboarding and Section 4.2 IEC
   approval processes in parallel
3. Secure a specific cloud-cost sponsor to close the open R7 sustainability
   risk (Section 7) before scaling past the initial pilot
4. Revisit this document once real clinical validation data (Section 4) is
   available — several sections (notably Section 1.6's impact metrics and
   Section 4.3's success criterion) should be updated from projected to
   actual figures at that point
