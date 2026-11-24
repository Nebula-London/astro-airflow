# Reviewer lessons (read at step 5 for any defence, government or high-assurance JD, or after a rejection)

Source: six rounds of feedback from a recruiter-side technical reviewer on CVs built by this skill for a DV-cleared
defence data engineering role. The score never moved from 45/100, but the *flags* show what makes a truthful CV read
as generic. Most were avoidable at drafting time.

## 1. What went wrong with the first drafts (root causes in this skill)
1. **The writer never saw the specifics.** The digest ranks and truncates bullets, shows near-duplicates once, and
   drops low-score roles. Facts that exist in the master (15,000 crew; a 40GB transfer; a 70% hardware cut; a team cut
   from five engineers to two; data contracts enforced at one client; the exact AWS chain for a pipeline) never reached
   the draft. Drafts were built from adjectives instead. Fix: `dump_roles.py` (SKILL.md step 2b), and use its FACTS lines first.
2. **Tool-dropping.** Lists of tools with no "how" or "why". Fix: every bullet names what was built, with which tool,
   for whom, plus any number the master holds. A tool that appears only in a technologies list is not a claim.
3. **Architecture labels without mechanism.** "Medallion", "zero-trust", "event-driven architecture", "cloud-agnostic",
   "serverless architecture", "scalable platform" were each flagged as buzzwords because the CV did not name the
   mechanism (storage format, identity provider, schema registry, compute service...). Fix: standing rule 12.
4. **Overlapping dates looked like fabrication.** The member is an independent contractor with parallel engagements;
   the CV never said so. Fix: standing rule 15 (one framing line, separate "parallel" block, employment type as
   the master records it).
5. **Coined or product names used as proof.** An internal pipeline acronym expanded in brackets was called nonsensical;
   a product name was called a substitute for technical spec. Fix: standing rule 13.
6. **Empty adjectives.** enterprise-level, scalable, robust, innovative, production-grade, "Data as a Product",
   Domain-Driven Design, sovereign, target-state, data strategy, operational control, architectural guidance,
   production stability, operational support, optimisation, interoperability, maintainability. `lint_content.py`
   now WARNs on these. Replace each with the concrete fact behind it, or delete.
7. **False "no evidence" from the digest.** EVIDENCE showed None for Kubernetes and for open-source although the master
   has both (weighted `*3` alternations can miss). Fix: standing rule 18.

## 2. Claim-mechanism checklist (apply to every architecture or capability phrase)
For each phrase, the CV must give at least one of: the named tool or service, the data volume or scale, the number
of users/systems, the sequence of components, or a before/after figure, **all taken from the master or the member's
answer**. If none exists:
- describe the concrete component instead ("Teradata extract on EC2 scheduled by cron, S3, Lambda into RDS PostgreSQL"), or
- drop the label and keep the plain activity, or
- ask the member (question batch) for the mechanism and one number.
Never invent a mechanism to satisfy a reviewer.

| Label | What the reviewer wanted | Ask the member |
|---|---|---|
| medallion / gold layer | schema-enforcement mechanism, de-duplication logic, layer validation | tool or constraint used; merge key or window rule |
| event-driven / Kafka | partitions, consumer groups, schema registry, patterns | which of these, and message volume |
| cloud-agnostic / multi-cloud | the abstraction layer (Terraform modules, K8s distribution, operators) | what actually ran on each cloud |
| zero-trust / least-privilege | identity provider, mTLS, policy decision point | concrete controls only |
| low-latency tuning | the mechanism (kernel bypass, CPU pinning...) and latency figure | figure and mechanism |
| serverless | which compute service and how state was handled | service name |
| data quality rules | which rules, which tool, what failed builds | two example rules |
| optimisation (DAGs, cost) | before/after runtime or cost | one figure |

## 3. Question batch additions (step 3)
Besides missing JD terms, ask at most three **evidence questions** for the member's strongest claims: "mechanism and one
number for X". Also ask: (a) is the engagement part-time or concurrent where dates overlap (if the master is silent);
(b) nothing about clearance expiry: an active clearance qualifies the member, and a new cleared role extends it to the
end of the contract (SKILL.md rule 11); (c) permitted wording for defence/government
work (rule 17). Record answers in answers.json like any other.

## 4. Dates
Never alter dates. Use today's real date: roles ending or starting "this year" are valid. A reviewer working from an
older date may call recent dates "impossible" (this happened for May-Oct 2026 when it was already 15 Oct 2026). That is
a reviewer error; tell the member in the hand-over, do not edit the CV to hide it, and suggest mentioning the date to a
human recruiter.

## 5. Reviewer feedback loop (when the member pastes feedback)
1. Classify every flag: **A** wording/evidence problem the master can fix; **B** needs facts only the member has;
   **C** reviewer error or unfixable (impossible-date flag, claim contradicted by the master).
2. Apply all A fixes in one build. Ask the B questions once, in one batch. Report C items plainly with the reason.
3. Keep evidence: do not delete true, specific facts to dodge a flag (e.g. keep Lambda/S3/RDS chain, numbers). Remove or
   reword only the label that lacks a mechanism.
4. Stop after two rounds on the same reviewer. If the score and the headline flags do not change after real fixes, say so
   and hand back the list of facts only the member can supply. Do not keep stripping the CV.
5. Re-lint (`lint_content.py`) before every hand-over; fix all WARNs from the lists above or explain them.

## 6. Defence and security-cleared roles
- A required "previous defence experience" with no CV-safe evidence is a **gap to report**, not a thing to imply. The
  master's clearance sponsor (e.g. MOD) is data: never put it on the CV and never infer defence experience from it.
- If the member says they have worked on defence work but cannot disclose details, do not mention it unless they give
  approved wording (for example "Worked in UK government security-cleared environments; details withheld"). Offer to
  add that wording; if declined, say in the hand-over that the essential has no evidence on the CV.
- Security assurance work the master *does* evidence (ISO 27001 and Cyber Essentials Plus programme, penetration
  testing, SAST/SCA/DAST in CI/CD, encryption of PII) is the best honest bridge; state it with its mechanism.

## 7. Titles
The headline and Profile may use only titles held in the master (lint WARNs). Do not call the member Lead/Principal/
Architect unless a role title says so; do not claim "Lead" across engagements that run in parallel unless the master
titles say so.
