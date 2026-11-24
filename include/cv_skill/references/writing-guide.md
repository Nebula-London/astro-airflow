# Writing guide (load at step 4; do not reload)

## Content selection (decided by the TIMELINE PLAN; adjust only via plan.json)
- **Main list:** relevant roles across the candidate's career, selected by relevance, demonstrated capability, ownership, complexity and impact. Use a continuous timeline, newest first, with no overlapping roles side by side. Do not use years of experience to determine which roles deserve inclusion.
- **Other engagements:** one subheading after the main list (and after any "Earlier roles" if applicable). First the relevant roles that overlap a main role, with full detail; then one line ("Also: Company (years), ...") for low-relevance roles.
- **Depth by relevance:** strongest evidence of capability and impact gets 4-6 bullets (max 7); moderate evidence gets 2-4; limited or less relevant evidence gets 2-3 bullets or a 1-2 line `para`, regardless of career stage or role dates. Full-detail other engagements: 2-4 bullets.
- **Dates are never altered** to hide an overlap; overlaps of a few weeks inside the main list are tolerated (flag them in the hand-over).
- **Brief gap-fillers** (`"brief": true` in plan.json, marked `B` in the digest): low-relevance roles that keep the main timeline continuous. Write a 1-line `para` from the role's bullets (fetch them with `--roles`); never build it from the technologies list alone. Do not automatically collapse significant career roles (e.g. 1-2+ year engagements) into a single line without checking if relevant tools/experience can be drawn out. Ask the user what tools, platforms or accomplishments were used during that period that align with the target role, incorporate them as full bullets, and persist them back to the master JSON with `$CV save-master`.
- A role under both a contracting entity and a client: show the client.

## content.json schema (no need to open the example)
```
{"name", "headline", "contact": {"location","email","phone","linkedin","website","calendar"}, "meta": {"title","author"},
 "sections": [{"heading": "...", "items": [
   {"type":"paragraph","text":"...","space_before":0}, {"type":"labelled","label":"...","text":"..."},
   {"type":"subheading","text":"..."},
   {"type":"role","ref":N,"title":"...","company":"...","bullets":["..."] | "para":"..."}]}]}
```
Markup: `<b>`, `<i>`, `<a href="URL">label</a>`. Education entries are `role` items with no `ref`, no bullets (`right` = years).

## Page budget (2 full pages)
Target exactly 2 clean, high-impact pages, with both pages filled to 90-100% of usable page space. Experience length and role count must be determined by the strength, relevance, complexity and impact of the candidate's evidence, not years of experience.
- Prioritise roles demonstrating relevant technical depth, ownership, architectural decisions, automation, reliability and delivery outcomes. Include fewer roles with stronger evidence rather than more roles simply to demonstrate career duration.
- Do not leave page 2 partially empty when relevant, evidence-backed content exists. If either page is below 90% fill, first expand the most JD-relevant bullets with concrete technical specifics (mechanisms, scale, metrics, tools and outcomes) from the master; then review whether relevant profile, capabilities, experience, education or certifications have been omitted. Never invent, duplicate or add unsupported content merely to fill space.
- If the available evidence is genuinely insufficient to fill two pages, ask the user for additional relevant detail rather than padding the CV. Renderer exit code 2: trim the least relevant bullets or roles only when content overflows; never shrink the font range.

## Sections (model: `references/example-content.json`)
1. **Header** (renderer builds it): `name`, `headline` (JD-aligned, only titles/capabilities the master supports, about 70 characters max so it stays on one line), `contact` dict (location = city, country; email; phone; linkedin; website; calendar = the master's `profiles.calendar` booking URL, rendered last as "Book an interview" so recruiters can see availability and book a slot; keep it exactly as pre-filled).
2. **Profile:** 4-6 lines, fresh for this JD. Sentence 1: demonstrated seniority/capability + core skill + domain. Establish seniority through evidence of ownership, complexity, technical depth and impact, not years of experience. Mention years only when explicitly required by the JD and supported by the master; never use years to infer seniority. If years are explicitly required by the JD, use only the digest's `~Ny` / plan.json `years` and state them factually. Years are a factual eligibility detail, not a measure of capability or seniority. Then the best matching outcome with the employer named, then current hands-on work, then right to work if relevant. Do not mention security clearance anywhere: the single clearance line is added by `arrange_roles.py` from the plan (minimum disclosure). Bold at most one domain phrase. **Voice: implied first person, no pronouns** (UK convention): never "I", "my", "he/she", and never third-person verbs ("Uses", "Coaches", "Builds"). Past tense for earlier roles ("At Example Bank, led ..."), base-form present for current work ("Coach client engineers ...", "Use AI coding tools ..."); lint fails on both. Only state activities that a bullet supports (tech-list-only items belong in Core Capabilities).
   - **Sector bridge:** if the JD's sector has no evidence, describe adjacent clients factually by sector ("consumer price-comparison website Example Compare"); never claim the sector itself.
3. **Core Capabilities:** 4-6 `labelled` lines, labels mirroring the JD's keyword groups, ordered as the JD orders them; evidence-backed items only, JD terms first within each line. Calibrate item lengths so each capability spans across the line (aiming for 1.80-1.95 lines for 2-line entries, or full 1-line fills) to prevent awkward trailing wraps and empty white space on the right margin.
4. **Professional Experience:** `role` items with `ref`, `title`, `company`, bullets or `para`; `right` and `company_url` are filled from the plan. Use a short company name (no Ltd/LLP/"formerly"). Ensure bullets span complete lines (clean 1-line or full 2-line wraps) without leaving ragged 1.2-1.3 line trailing text.
5. **Education & Certifications:** formatted with consistent non-bold bullet points (`{"type": "bullet", "text": "..."}`) for both degrees and certifications on one line each. Degrees include the institution as a clickable link from `institution_url` (e.g. `Master's degree MSc — <a href="...">University</a> (years)`). Certifications include clickable issuer badge links from `credential_url` (e.g. `Certification Name — <a href="...">Issuer</a>`). Label courses/training as such, not as certifications, and give no grades. **List the certifications relevant to this JD, expired ones judged exactly like current ones** (they show the study and the exam passed; renewal fees are often skipped once real-world experience exists); leave unrelated ones out to avoid bloat, they stay in the master. Order: degrees first, then most JD-relevant certifications. **Never show expiry or validity dates, or the word "expired"**; issue years are optional.

## Bullet quality (top-quality text; `lint_content.py` enforces the checkable parts)
- Start with a strong past-tense verb (present tense for the current role, base form: "Build", never "Builds"); one idea per bullet; 1-2 rendered lines.
- Pattern: **action + what + technology + complexity/ownership + outcome/scope**, using only facts in the master or the user's answers. Prioritise evidence of decisions, problem-solving, technical depth and measurable impact over duration of involvement. Keep numbers and hedges exactly as the master states them.
- Put JD vocabulary first where it honestly describes the same activity; never change meaning.
- No repeated bullets across roles; vary verbs; avoid duty phrasing ("responsible for", "managed testing", "participated in") and verbless fragments ("Multi-format ingestion, ..."); at most one semicolon per bullet.
- Write `&` bare, never `&amp;`.
- Titles exactly as in the master; certifications exactly as named and typed in the master (a course stays a course).
- UK spelling. Plain hyphen "-" in date ranges; en dash only in prose. No arrows, emoji, smart symbols outside WinAnsi (the checker fails on them).
- Markup: `<b>`, `<i>`, `<a href="URL">label</a>` only; bare `&` is fine.

## Links
- `company_url` from the master's `*_website`/`url` fields. If missing, look up the official site once, confirm it resolves, and list it in the hand-over as "looked up". Never guess. Skip defunct entities and contracting entities.

## Page fill & line widths
- Target both pages at 90-100% of usable page space. If either page is below 90%, add evidence-backed depth to the most JD-relevant roles and check for omitted relevant content across all sections. Do not use filler, repetition or unsupported claims to reach the target. If the master does not contain enough relevant evidence, ask the user for more information. Renderer exit code 2: trim the least relevant bullets or roles only when content overflows; never shrink the font range.
- **Minimize trailing whitespace deterministically:** aim bullets and Core Capabilities at either the 1-line or 2-line character windows printed by `cv skeleton` (`LINE BUDGETS`), avoiding the half-filled line-2 dead zone (~105-145 chars at 10.5pt). After `cv build`, run `cv layout SLUG` once if any trailing line is <74% full to get exact `+N / -M chars` targets from ReportLab.

## Specific-over-broad bullets (from reviewer feedback)
- Weak: "Built event-driven architecture using Kafka." Strong: what flowed, in what volume, which consumers, which guarantees - from the master or the member. If the master has only the tool, write "Used Kafka for X" and nothing grander.
- Weak: "Designed cloud-portable deployment approaches across four clouds." Strong: what ran where (e.g. Terraform-provisioned AWS with EKS and MWAA; a serverless API on GCP). Claim only what is evidenced per cloud.
- Weak: "Defined the target-state architecture and data strategy." Strong: the decisions made and their scope (e.g. possible Databricks migration), and the concrete rules written (e.g. data quality rules applied as dbt tests).
- Contractors: one framing line under Experience; overlapping engagements in a separate labelled block; part-time/concurrent only as recorded in the master.
- Security assurance: state the programme and the mechanism (ISO 27001 and Cyber Essentials Plus, internal penetration tests, NCSC hardening, SAST/SCA/DAST in CI/CD, PII encrypted at column level), not "security assurance work".

## Pitfalls seen in practice
- Years of experience were incorrectly used to infer seniority, capability or role importance. Assess each role and achievement independently based on demonstrated ownership, technical complexity, decision-making, relevance and outcomes. A candidate with fewer years can demonstrate greater capability than someone with a longer career.
- Drafts drifted into detail the user never gave (e.g. a guessed purpose for a deployment): ask instead.
- Sources disagreed on dates, titles or grades: ask which is right before using either.
- Users correct themselves mid-clarification: use the latest answer everywhere (CV and master).
- A tool confirmed for one role was spread to others: apply scope exactly as given.
- The digest hid role-specific facts (numbers, named chains, employment type). Always run `dump_roles.py` for planned roles.
- Empty adjectives and unexplained architecture labels were the most common reviewer flags; see `reviewer-lessons.md`.
