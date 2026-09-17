# Slide-link knowledge base

How to decide whether a candidate URL is **real lecture slides/notes for a specific
course** (ACCEPT) or **not** (REJECT). Distilled from the human annotations in
`correct.txt` (201 courses, one known-good link each) and `wrong.txt` (344 courses, both
auto-suggested links are wrong). Counts below are how often a signal appeared among the
correct links vs. the wrong links, so each rule is grounded in the data.

## The core question
A link is CORRECT only if it leads to **actual lecture material** — a slide deck
(`.ppt/.pptx`, or a lecture `.pdf`), a page that *lists* the decks (`/lectures/`,
`/slides/`, `/notes/`), or a real course site that leads to them — **for this course at
this university**.

A link is WRONG if it is an **administrative page** (catalog, registrar, bulletin,
course-info, syllabus PDF, library guide, schedule search) or points to a **different
course / different university** with only a coincidental code match, or a generic GitHub
repo (homework / README) with no decks.

---

## POSITIVE signals — link IS slides  (freq: correct vs wrong)

1. **Slide/lecture directory in the path** — `/lectures/` (21 vs 10), `/slides/`
   (25 vs 12), `/notes/` (13 vs 2), `/lecture_slides/`, `/handout(s)/`, and the file
   forms `lectures.htm(l)`, `lect.html`, `handouts.shtml`, `notes.htm`.
   - `https://courses.cs.duke.edu/spring26/compsci330/lectures.html`
   - `https://home.cs.colorado.edu/~ketelsen/files/courses/csci3022/slides/`
   - `https://www.cs.cornell.edu/courses/cs2800/2009fa/notes.htm`
2. **Direct deck files** — path ends `.ppt` / `.pptx` (always a deck), or `.pdf` whose
   **filename** looks like a lecture: `lecture`, `lec`, `slide(s)`, `handout`, `lesson`,
   `chapterNN`/`chNN`, `LNN`/`L0`, `intro`/`introduction`, `welcome`, or a numeric lecture
   prefix like `01-…`, `sd01…`.
   - `.../slides/01-intro.pdf`, `.../Slides/01%20JavaIntro.pdf`,
     `.../cse435_Lecture_1.pdf`, `.../CS344_Lesson2_Slides.pdf`,
     `mpslab-asu.github.io/publications/slides/Kim2019VLSID.pptx`
3. **Faculty `~user` pages on a dept/edu host** with the course code (66 vs 114 — common
   to both, so it must be combined with a slide/lecture/term signal, and must be the
   *right* university).
   - `https://www.eng.auburn.edu/~xqin/courses/comp2710/fall09/lectures.htm`
   - `https://www3.cs.stonybrook.edu/~pfodor/courses/cse114.html`
4. **Department course hosts** with course code (+ term): `courses.cs.<u>.edu/<code>/<term>/`,
   `cs.<u>.edu/~prof/<code>`, `web.stanford.edu/class/<code>`,
   `cseweb.ucsd.edu/classes/<term>/<code>`, `www.cs.cornell.edu/courses/<code>/<term>/`.
   - `https://courses.cs.washington.edu/courses/cse512/25sp/index.html`
5. **Course sites on Pages/vanity hosts** — `<user>.github.io/<code>/…` (31 vs 28),
   instructor `<name>.com/teaching/<code>/`, or a course's own domain.
   - `https://calpoly-iandunn.github.io/csc476/lectures/`, `http://javiergs.com/teaching/cse240/`,
     `http://www.databaselecture.com/`, `https://csci1410-2023.vercel.app/`
6. **GitHub repo *with a deck inside*** — a `Slides/`/`Lecture*` folder or a
   `*Slide*.pdf` / `*Lecture*.pptx` / `*.pdf` deck file in the path (a bare repo root is
   ambiguous, see below).
   - `https://github.com/parthi2929/gt_cs7637/blob/master/Slides/02%20-%20Introduction%20to%20CS7637.pptx`
   - `https://github.com/udacity/cs344/blob/master/Lesson%20Slides/CS344_Lesson2_Slides.pdf`
7. **OER / OCW / lecture-note repositories** — `ocw.*/…lecture-notes`,
   `academicworks.cuny.edu/…oers`, a blog's `model-slides`.
8. **A term token** (`fall09`, `sp25`, `24SP`, `2009fa`, `S12`) strengthens confidence
   that the page is a real offering.

---

## NEGATIVE signals — link is NOT slides  (freq: correct vs wrong)

1. **Catalog / registrar / bulletin / course-info** (near-exclusive to wrong):
   `catalog` (1 vs 49), `registrar` (0 vs 38), `course-descriptions` (0 vs 18),
   `course-listings` (0 vs 18), `courseinfo` (0 vs 12), `ViewCatalog` (0 vs 13),
   `bulletin` (0 vs 4), `course-offerings`, `academiccalendar`, `preview_course`,
   `courses_list`, `ShowCourse`, `.p_showform`, `programs/bpid`, `coursicle`.
   - `https://www.baruch.cuny.edu/courseinfo/detail/CIS2200`,
     `https://bulletin.case.edu/course-descriptions/csds/`,
     `https://webapps.case.edu/registrar/course-listings?...`
2. **Syllabus documents** — `syllab` in a **PDF filename** (`*-Syllabus-*.pdf`),
   `viewsyllabus`, `/syllabi/`, `model-course-syllabi`, a course-`manual` PDF.
   - `https://scai.engineering.asu.edu/wp-content/uploads/.../CSE-205-Syllabus-SP25.pdf`,
     `https://webapp4.asu.edu/bookstore/viewsyllabus/2207/76228/pdf`
3. **Class-schedule / schedule-search** on a registrar host: `class-schedule`,
   `schedule/search`, `szkschd`, generic `/schedule/`.
   - `https://www.brandeis.edu/registrar/schedule/search`,
     `https://gulfline.fgcu.edu/pls/fgpo/szkschd.p_showform`
4. **Library guides** — `libguides`, `libraryguides`, `library.<host>`.
5. **Bare university homepage / dept landing page** — path is empty or `/`, or a
   department `…/computer-science/courses/` *listing* with no deck.
   - `https://www.cpp.edu/`, `https://www.carleton.edu/computer-science/courses/`
6. **README / project / homework files**, esp. on GitHub — `README.md`/`README.pdf`,
   `Syllabus.md`, `*-HW`, `*-Project`, a stray `*.md` notes file.
   - `https://github.com/SGomez47/CSE464-2023-Sgomez47/blob/main/README.pdf`,
     `https://github.com/timnaimov/CIS-2300-HW`
7. **Marketing / online-program pages** — `courses.asuonline.asu.edu`,
   `onlineprograms.*`, plus the usual aggregators (`coursehero`, `studocu`, `scribd`,
   `slideshare`, `chegg`, `quizlet`, `coursicle`, …).

---

## Tricky / edge cases (require looking at page CONTENT, not just the URL)

- **Cross-university code coincidence.** A lecture PDF/site can match the code but belong
  to *another school* — WRONG unless the content is genuinely the same course/topic.
  - WRONG: Binghamton `CS110` → `https://web.stanford.edu/class/cs110/...lecture-1.pdf`
    (Stanford's CS110). WRONG: ASU `CIS300` → `people.cs.ksu.edu/~schmidt/300f02/Lectures/`.
  - ACCEPTED (rare, topic really matches): Bentley `CS605` (Software Engineering) →
    `vulms.vu.edu.pk/Courses/CS605/Downloads/SoftwareEngineeringII.pdf`; Laval `GIF1003`
    (C++) → `cs.virginia.edu/c++programdesign/slides/`.
  - Rule: same institution (host resolves to the course's university, or the university
    name appears in the host) ⇒ trust a slides path. Different institution ⇒ **AMBIGUOUS**
    → confirm by content (is it the same topic/level?).
- **GitHub repo roots look identical for right and wrong.** `/gongzhitaao/comp3220`
  (correct) vs `/paavanindela/CS215` (wrong) are structurally the same. Only a deck file
  or a `Slides/` folder in the path is a safe ACCEPT; a bare repo root is **AMBIGUOUS**
  (fetch the repo/Pages site to see if decks exist). Even `/CS571-S25/lectures` was WRONG.
- **"syllabus" is not always bad.** On a **course/Pages site** or faculty page,
  `/syllabus/` is often the schedule/lectures page (ACCEPT-leaning): e.g.
  `https://huichen-cs.github.io/course/CISC1115/24SP/syllabus/`. A **`*Syllabus*.pdf`** on
  an admin host is REJECT. A `.pptx` named `lect1_syllabus…` is a **deck** (ACCEPT).
- **`intro.pdf` vs `01-intro.pdf`.** A bare `intro.pdf` for the wrong course is WRONG; a
  numbered `01-intro.pdf` inside `/slides/` is a lecture (ACCEPT). Filename + directory +
  university together decide.
- **Course homepage with no visible slide path** (e.g. `https://cs.brown.edu/courses/cs015/`,
  `https://www.cs.cornell.edu/courses/cs2110/2026sp/`) is often correct but needs a fetch
  to confirm the decks are reachable (reduce/follow/append), so treat as **AMBIGUOUS**.

---

## Decision procedure (per candidate link)

Produces `accept` / `reject` / `ambiguous` (ambiguous is resolved by content, see below).
Precedence, top to bottom:

1. No host, or an **aggregator/blocked** host ⇒ **reject**.
2. **Deck extension:** `.ppt`/`.pptx` ⇒ **accept**. `.pdf` whose filename contains
   `syllab`/`README`/`course-manual`/`fact-sheet` ⇒ **reject**; `.pdf` with a lecture-ish
   filename or inside a `/slides|/lectures|/notes/` dir ⇒ **accept**; otherwise a `.pdf`
   with an unknown name ⇒ **ambiguous**.
3. **Hard-negative** catalog/registrar/bulletin/course-info/course-listings/
   course-descriptions/viewcatalog/academiccalendar/coursicle/**syllabus-doc**/
   class-schedule/schedule-search/**libguides** in host or path ⇒ **reject**.
4. **Bare homepage** (path empty or `/`) on a university host ⇒ **reject**.
5. **`github.com`:** README/`.md`/`-HW`/`-Project`/`Syllabus` ⇒ **reject**; deck file
   (handled in 2) ⇒ **accept**; anything else (repo root, `/tree/…`) ⇒ **ambiguous**.
6. **Positive slide path** (`/lectures|/slides|/lecture_slides|/notes|/handout`,
   `lectures.htm`, `handouts.shtml`, `notes.htm`) on a university/course host:
   **same institution** (host resolves to this college, or the college name/course code is
   in the host) ⇒ **accept**; **different institution** ⇒ **ambiguous** (topic check).
7. **Course/Pages host with the course code** but no explicit slide path (course homepage)
   ⇒ **ambiguous** (fetch to confirm).
8. Anything else ⇒ **ambiguous**.

### Resolving `ambiguous`
1. **Fetch** the page (rendered) and run `find_slides()`; if it yields real decks ⇒
   ACCEPT. Try `reduce → follow one lecture/schedule sub-page → append /lectures//slides/
   → bump the year` (the existing resolver's `manipulate()`), then re-check.
2. If a fetch is still inconclusive, or the university differs (topic judgment needed),
   **escalate to a subagent** that reads this file, opens the link(s), and decides which
   candidate — if any — is real slides for *this* course, returning a short reason.
3. If both candidates end up rejected/inconclusive ⇒ record `no_valid_link`.

### Picking between two candidates for one course
If both accept, prefer: (a) more decks found on fetch, then (b) an explicit `/slides|
/lectures` path, then (c) course-code + term match, then (d) the higher `score_link`
score. Ties break to `link_1` (the auto-labeler's top pick).
