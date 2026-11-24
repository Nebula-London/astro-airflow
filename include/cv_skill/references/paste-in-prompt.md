# Paste-in prompt (use if the skill is not installed)

**No master yet?** First upload your current CV and `example-master.json` (from this folder) and paste:
"Build my master resume JSON in exactly the structure of example-master.json from my CV. Facts only: copy every role,
date (YYYY-MM-DD or YYYY-MM), bullet, number and technology as written; leave anything unknown empty and ask me about
missing dates or clients in one batch. Remove `_readme`. Give me the file as <first>-<last>-v1.json." Keep that file
private (do not share it or commit it anywhere), then use the prompt below with it.

Here is a job description (below). My master resume is the .json file I have uploaded with this message, or part of this session/chat.

Before drafting anything, compare the JD with my master and ask me one short batch of clarifying questions (max about 6): a grid of keywords where my evidence is partial or missing (ask "none, or tool / which role / what you did"), what to emphasise, logistics the JD needs (clearance, location, availability), borderline roles, older-than-10-year roles that look like niche matches, and the headline I want. Also scan my master for anachronistic tools, conflicting dates, duplicate bullets, title typos and wording, and include those findings in the same batch. Skip anything the master already answers. End with the assumptions you will use if I say "proceed", then wait for my reply.

After I answer, create a very targeted 2-page A4 PDF CV:
- Facts only. Reword to highlight JD keywords but never add tools, numbers, dates, titles or qualifications that are not in my master or my answers; use new facts only in the roles I name.
- Reverse chronological. Last 10 years, plus older roles only for a niche match (under a short subheading); use the extra space to give JD-relevant roles more depth, not filler. Drop irrelevant roles and mention them in one "other engagements" line, with extra space above it.
- Show client names, not contracting entities. Write a fresh profile for this job (my master summary is intentionally empty). Core capabilities mirror the JD's keyword groups, evidence-backed only.
- Header is exactly 3 lines: name; JD-aligned headline; then `Location | email (mailto link) | phone (tel link) | LinkedIn (link) | Website (link) | Book an interview (link to the calendar URL in my master's profiles.calendar, if present)`. Nothing else in the header (put clearance etc. in the Profile). No page footer.
- Education: qualification, institution and years only, no grades or classes. Make company, college and certification names clickable using URLs from my master or confirmed official sites (never guessed); list the links you added.
- Do not claim "no career gaps", rate or IR35 status.
- UK spelling, A4, no photo. Check both pages and all links after rendering.
- Finish with: roles included/dropped, JD keywords I did not claim, and anything I should verify.

Never modify my uploaded master file. If I approve changes to it, write them to a new version-bumped JSON file, diff it against the original, and give me the new file.

JD:
- Write the Profile and bullets in implied first person with no pronouns (no "I"/"my") and no third-person verbs ("Uses", "Coaches"): "Led ..." for past work, "Lead ..." for current work.
