# Batch build brief (phase 3) - follow exactly, no questions to the user

Run every command as `bash {SCRIPTS}/cv <command>` (works in a fresh shell; no setup needed).
If you share a machine with other batches, prefix commands with `CV_WS={WORKSPACE}`. Output: `{OUT}/<slug>/`.
Read once: `{OUT}/answers.json` (confirmed facts: evidence ONLY in the roles listed; `"has": false` = never claim)
and `{SCRIPTS}/../references/writing-guide.md`. Never read or edit the master JSON; never edit the scripts.

## Per JD (in the order given)
Below, `cv.py X` means `bash {SCRIPTS}/cv X`.
1. Read `<jd_dir>/<slug>.txt`; write 15-30 keywords (`a|b` synonyms, `term*3` must-haves + domain).
2. `cv.py digest <slug> --keywords "..."` (prints CLEARANCE, EVIDENCE, TIMELINE PLAN; writes plan.json).
   Optional: `cv.py digest <slug> --keywords "..." --section contact,education,certifications` once per batch.
3. `cv.py roles <slug> <refs> --keywords "..." && cv.py skeleton <slug>` (bullets for the roles you will write, then
   the pre-filled content with TODO slots: contact, roles in plan order, education, JD-relevant certifications).
4. Write `{OUT}/<slug>/content.json` once with the file-write tool, filling every TODO (lint fails on leftovers).
   Use `arrange_roles.py - plan.json --move N=main|other_line|other_detailed --plan-only` only to fix placement.
5. `cv.py build <slug>`: arrange, lint (with answers), render, check, and report.json. Fix every FAIL/WARN printed and re-run.
   Under 85% fill: add real depth to the most relevant roles. Render exit 2: trim least relevant bullets.

## Hard rules
- Facts only from master bullets/tech lists or answers.json (only in the roles named). Keep hedges; no new numbers.
- An answer's `detail` wording is the ceiling of the claim (e.g. "applied X principles" never becomes "certified").
- `"has": false` beats the tech list: never show such a term anywhere, Core Capabilities included, even if an old tech
  list names it (e.g. TOGAF when "enterprise architecture" is no, Spring when "spring" is no).
- Identical JDs (same text, other agency): build once, copy content.json, change only the PDF title; say so in the report.
- No descriptive add-ons of your own ("for a vehicle finance lender", "for real-time data distribution"): if a phrase is
  not in the master or answers, leave it out rather than flagging it in the report.
- Never write clearance text: arrange_roles adds the single line (plus a confirmed NPPV level when the JD asks).
- Answers without roles (e.g. a reservist or sector note) may appear as ONE Profile sentence, only when the JD values it.
- Profile and bullets: implied first person, no pronouns ("I", "my"), no third-person verbs ("Uses", "Leads"): past tense for earlier roles, base-form present for current work.
- Profile: no role noun the member never held ("architect", "manager"); years only from digest `~Ny`.
- Headline: a held title + " | " + JD capabilities, <= ~70 chars. UK spelling for UK roles. Bare "&".

## Report back (one line per JD + gaps)
`<slug> | <pdf> | <font>pt | <fill>% | essentials with no evidence | items to verify`. Also write the
essentials with no evidence to `{OUT}/<slug>/gaps.md`.
