# Workflow details (read only when needed)

SKILL.md covers the single-JD fast path. This file holds the batch workflow, the manual script-by-script steps
(fallback when `cv prep` cannot be used), the tool table per agent, install and maintenance.

## Environment and tools per agent
The skill needs only: a shell that runs `bash` (the launcher finds Python 3.14, minimum 3.12, or fetches it into the job-search folder with uv), file read/write, and a way to talk to the user. Every
command goes through one launcher that works even when each command starts in a fresh shell (no `source`/`export`):
```
CV="bash <SKILL_DIR>/scripts/cv"      # SKILL_DIR = the folder containing this SKILL.md (write it out in full)
$CV env                               # once: resolves python (+ installs reportlab/pypdf if missing), prints paths
$CV run <script.py> [args]            # any script, e.g. $CV run digest_master.py MASTER.json --keywords ...
$CV run dump_roles.py MASTER.json --roles 1,2,5   # full per-role detail + FACTS (numbers); never skip for planned roles
$CV <cv.py command>                   # batch helpers: init, triage, next, answer, digest, roles, build, final, brief, save-master
```
- `OUT` (outputs): `/mnt/user-data/outputs/` if it exists, else `cv-out/` next to the master. Uploads may be in
  `/mnt/user-data/uploads/` (Claude.ai). If `pip install` is blocked and reportlab/pypdf are missing, say so and stop.
- Use whatever tools the agent provides, by capability (names differ per agent):

| Capability | Claude.ai (web) | Claude Code | OpenCode | Cortex Code | If missing |
|---|---|---|---|---|---|
| Fetch a JD URL / confirm a site | `web_fetch`, `web_search` | `WebFetch`, `WebSearch` | `webfetch`, `websearch` | `web_fetch` | ask the user to paste the JD; skip the link |
| Multiple-choice questions | `ask_user_input_v0` | `AskUserQuestion` | `question` | `ask_user_question` | one plain numbered message, then wait |
| Give files to the user | `present_files` | state the absolute path | state the absolute path | state the absolute path | state the path |
| View a page image | `view` | `Read` | `read` | `read` | rely on `check_pdf.py` text checks |
| Parallel build agents | not available: build sequentially | `Task` | subagents, if configured | `task` | build sequentially |

Never skip a step because a tool is missing; use the fallback.

## Batch workflow (several JDs)
Once: `$CV init OUT --master MASTER.json --jd-dir JD_DIR` (remembered: all later commands take no paths; set
`CV_WS=OUT/workspace.json` only when running two batches at once). Job-board boilerplate (Posted by, agency, Reference, Function lines) is ignored by triage.

### JobServe roles from the jobserve-new-jobs skill
A URL containing `JobListing.aspx?shid=` or `JobSearch.aspx?shid=` is a search, not one JD. Fetching and de-duplicating a
search belongs to the separate **`jobserve-new-jobs`** skill (CV-independent and schedulable): it follows every role to its
detail page and writes a dated JSON (`<date>_jd-extracted-from-jobserve.json`) holding only roles not seen before. The user
may hand over such a JSON from a scheduled run. Import it here, then run the usual flow:
```
$CV import-jds <json> --jd-dir OUT/jds               # one <slug>.txt per role
$CV init OUT --master MASTER.json --jd-dir OUT/jds   # then triage -> next -> answers -> build
```
Each JSON role carries title, rate, location, IR35, duration, agency, skills, description and a ready-made `jd_text`;
`import-jds` writes the `<slug>.txt` files the batch workflow consumes. Review the roles and delete irrelevant `<slug>.txt`
files before `$CV triage`.
1. **Understand and rank (read every JD, build nothing).** Save each JD as `JD_DIR/<slug>.txt`, run `$CV triage`, and present the table: target (title, rate, duration, IR35, location, clearance), **Match %** and **Potential %**, best first, plus one line per JD on the lead angle. Ask the member to confirm the list (dropped JDs: delete the file, re-run).
2. **Questions, grouped.** `$CV next 4` prints up to 4 questions (highest-ranked JD first, essential before desirable, never a term or alias already answered), each with the JD line it came from. Ask them in **one** message using the multiple-choice tool (4 questions max; options No / Yes, with free text for details), or as one numbered plain message. If a JD line shows the term is not a skill (a team or product name), record `no` without asking.
   Record them all in one call: `$CV answers '[{"term":"X","has":true,"detail":"what you did","roles":[3,5],"also":["Alias1"]},{"term":"Y","has":false}]'` (single: `$CV answer "<term>" yes "<what you did>" --roles 3,5 [--also "Alias1,Alias2"]`).
   - `--also` marks terms answered by the same fact (OpenSearch → Elasticsearch, GenAI → LLMs) so they are never asked again.
   - A second `yes` for the same term **merges** roles and per-role details (use `--replace` to overwrite).
   - Act on the printed warnings before moving on: **NOTE** (other stints of the same employer were left out: confirm scope) and **WARN** (a credited role ended before the tool was released: drop the role or confirm).
   - "No" means no bullet; a tech-list mention may still appear in Core Capabilities.
   - Ask the clearance notes printed at the end (unmet level, NPPV: record `$CV answer NPPV yes NPPV3`). Never ask about renewal or expiry: an active clearance is extended when a cleared role starts. The member may say "skip" (record `no`) or "stop asking".
3. **Build every CV.** If the agent has subagents and there are 4 or more JDs, run parallel build agents (one per 2-3 JDs) with `$CV brief` as their whole prompt; otherwise (e.g. Claude.ai) build them yourself one by one, following the same brief. Each JD: `$CV digest <slug> --keywords "..."`, `$CV roles <slug> <refs> --keywords "..."`, `$CV skeleton <slug>`, write `content.json` from it, `$CV build <slug>` (arrange → lint with answers → render → check → `report.json`, one summary line). No further questions.
4. **Review, then final table.** Grep every Profile for over-claims the lint might miss, then `$CV final` (match before → after, clearance on the CV, unclaimed essentials, PDF). Present it with one line per JD on anything to verify.
5. **Offer to update the master:** list confirmed answers (term, roles, what was done). On approval: `$CV save-master OUT/<name>-v<N+1>.json [--only ...] [--skip ...]` (add `--dry-run` to preview). It appends per-role sentences and technologies, strips digest refs, and verifies an append-only diff. Answers with no roles (e.g. a reservist or sector note) are listed as skipped.

## Manual steps (fallback; `cv prep` / `cv skeleton` wrap these)

#### 1. Inputs
- JD: pasted, file or URL (fetch it). Missing JD or master: ask and stop.
- Extract JD keywords as one comma list: `a|b` for synonyms, `term*3` for must-haves and the domain (industry) so domain roles win overlaps, plain for nice-to-haves. Include the member's likely wording too (e.g. `PoC|proof of concept`). 15-30 keywords.

#### 2. Digest and evidence map
```
$CV run digest_master.py MASTER.json --jd JD.txt --keywords "Python,REST|RESTful,NoSQL|MongoDB,..." --plan $OUT/plan.json
```
One parse gives: contact, role outline (indices, dates, client, URL, tech), education, certifications, INTEGRITY findings, the EVIDENCE map (Strong / Partial / Skills-list only / None with role indices) and the **TIMELINE PLAN**, also saved to `plan.json`:
- **MAIN:** relevant roles from the last 10 years, newest first, chosen so no two overlap (>31 days) and the timeline has the fewest gaps.
- **OTHER ENGAGEMENTS - full detail:** relevant roles that overlap a main role.
- **OTHER ENGAGEMENTS - one line:** low-relevance roles (below `--min-score`, default 25% of the top score).
- **B** (brief) main roles fill timeline gaps; **EARLIER**: relevant roles older than the window; `years`: years per keyword.
Evidence tiers: **Strong** (in a bullet, recent), **Partial** (in a bullet, older), **Tech-list only** (named in a technologies list but no bullet says what was done: mention in Core Capabilities at most, ask before a bullet), **Skills-list only**, **None**. `~Ny` = years across matching roles (use for "N+ years" claims).
`plan.json` = `{"main", "other_detailed", "other_line", "earlier": [records], "years": {keyword: years}}`, each record `{ref, company, title, url, score, brief, start, end, right}`. Check main-list gaps the digest reports as "covered by [n]" and move roles if that reads better.
Then read only the roles you will write: `--roles <indices> --keywords "<same list>"` (prints the 8 most relevant bullets per role).

#### 4. Write `content.json`
Load `references/writing-guide.md` and follow it. Write `$OUT/content.json` in one pass: every experience role gets `"ref": <digest index>`; write role items for MAIN (brief ones as a 1-line `para`), full-detail and EARLIER roles only (no ordering, no other-engagements line, `right`/`company_url` may be omitted). Then:
```
$CV run arrange_roles.py $OUT/content.json $OUT/plan.json
```
Then lint (fix every FAIL; fix WARNs unless the member approved the wording):
```
$CV run lint_content.py $OUT/content.json MASTER.json --plan $OUT/plan.json
```
arrange_roles orders the section (MAIN newest first, then "Earlier roles with directly relevant experience", then "Other engagements" with the full-detail roles followed by an auto-built one-line list), fills dates and URLs from the plan, and warns about planned roles with no item.

#### 5. Render and check
```
$CV run render_cv.py $OUT/content.json "$OUT/<Name>-CV-<Role>.pdf" --pages 2
$CV run check_pdf.py "$OUT/<Name>-CV-<Role>.pdf" --pages 2 --content $OUT/content.json
```
- Renderer: auto-fits 13-8.2pt; exit 2 = trim content; prints last-page fill (target 85-100%) and header warnings (shorten headline).
- Checker: page count, 3-line header, left margin, orphaned headings, unsupported glyphs, mailto/tel, calendar booking link (when contact.calendar is set), link list. Fix every FAIL and re-run.
- Once checks pass: `--images .jobsearch/tmp/cv-check` (60 dpi default), view the pages once, then delete them. In batch mode skip images when lint and checks pass.
- Give the PDF to the user.

#### 7. Saving to the master (only on approval)
Show the change list; apply approved edits programmatically to the parsed JSON (never retype); write `$OUT/<name>-v<N+1>.json`; re-read, diff field by field against the original, report every difference (only approved edits may differ); give the user the file. For answers recorded with `batch_triage.py`, use `scripts/save_answers_to_master.py MASTER answers.json OUT` (or `cv.py save-master`), which does all of this.

## Install (per agent)
- Claude Code / OpenCode / Codex / Cortex Code: from the `omni-skills` library, `./bin/skills-install.sh jobserve-new-jobs
  targeted-cv-from-master` (symlinks both skills, side by side, into every detected agent; `-a claude,cortex` to pick,
  `--dry-run` first). Outside the library, `bash scripts/install.sh claude|opencode|cortex|agents|all` copies this folder
  to the agent's skills directory instead; `--project DIR` installs per project.
- Claude.ai (web): `bash scripts/package.sh`, then upload `dist/<name>.zip` in Settings > Capabilities > Skills (code execution on).
- Any other agent: paste `references/paste-in-prompt.md` into the chat with the master JSON (no scripts; lower accuracy).

## Maintenance (when changing the scripts)
Run `$CV run selftest.py MASTER.json JD_DIR [--content content.json]` over a folder of JD `.txt` files. It runs clearance + digest (+ arrange/lint) for every JD, the single-JD fast path (prep, answers, pending save, skeleton, build) in a scratch workspace, and compares with `JD_DIR/selftest-expected.json`; record a reviewed baseline once with `--record`. Keep JDs and baselines outside the skill folder (they contain member data).
- Edit the scripts only in the `omni-skills` library (`skills/targeted-cv-from-master/`), never in an installed copy: library installs are symlinks, so the edit is live everywhere; re-run `scripts/package.sh` for zip uploads.
- Missed JD terms: add them to `references/jd-lexicon.txt`. Tool dates for the answer sanity check: `references/tool-release-years.json`.
- Lint takes `--answers answers.json` so confirmed answers count as evidence; arrange takes `--answers` for the NPPV extra.
