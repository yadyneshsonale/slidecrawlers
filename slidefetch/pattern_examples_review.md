# Pattern review: top-20 links vs. your chosen slide URL

For each CS example (>= 4 ratings) below: your pick, my pattern read, and the top-20 search results scored by the resolver's rules.
Fill in the `YOUR REASON` blank in your own words so the heuristics can be tuned to match how you actually choose.

====================================================================================================
## [1] COMP2710 - Auburn University   (ratings=76, CS+>=4=YES)
- YOUR PICK : https://www.eng.auburn.edu/~xqin/courses/comp2710/spring10/lectures.htm
- MY READ   : faculty ~user page on the .edu, course code + term + 'lectures' in path
- code variants looked for: ['comp-2710', 'comp2710', 'comp_2710']  num=2710

  YOUR REASON (type here): ______________________________________

  >> your pick's host appears at rank(s) [1, 2], but the exact page is NOT in the top 20 -> you reached it by link manipulation (path/term edit).
----------------------------------------------------------------------------------------------------
   1. [ score=20] https://eng.auburn.edu/~xzq0001/courses/comp2710/lectures.html  <<<< same host as your pick
        reason: academic-tld,code:comp-2710,kw:lecture,univ,faculty
   2. [ score=12] https://www.eng.auburn.edu/~xqin/courses/comp7500/lectures.htm  <<<< same host as your pick
        reason: academic-tld,kw:lecture,univ,faculty
   3. [ score= 9] https://github.com/haydenwalker-git/Comp2710-Files
        reason: github,code:comp-2710
   4. [ score= 9] https://github.com/haydenwalker-git/Comp2710-Files/blob/main/README.md
        reason: github,code:comp-2710
   5. [   REJECT] https://www.coursehero.com/sitemap/schools/116-Auburn-University/courses/447772-COMP2710/
   6. [   REJECT] https://slidetodoc.com/comp-2710-software-construction-dr-xiao-qin-auburn/
   7. [   REJECT] https://www.studocu.com/en-us/course/auburn-university/software-construction/1773747
   8. [   REJECT] https://www.coursicle.com/auburn/courses/COMP/2710/
   9. [   REJECT] https://www.slideshare.net/slideshow/comp2710-software-construction-header-files/46830964
  10. [   REJECT] https://slidetodoc.com/comp-2710-software-construction-pointers-dr-xiao-qin/

====================================================================================================
## [2] CS143 - Stanford University   (ratings=5, CS+>=4=YES)
- YOUR PICK : https://web.stanford.edu/class/cs143/lectures/
- MY READ   : stanford /class/<code>/lectures/ dept pattern
- code variants looked for: ['cs-143', 'cs143', 'cs_143']  num=143

  YOUR REASON (type here): ______________________________________

  >> your pick's host appears at rank(s) [1, 2], but the exact page is NOT in the top 20 -> you reached it by link manipulation (path/term edit).
----------------------------------------------------------------------------------------------------
   1. [ score=15] https://web.stanford.edu/class/cs143/  <<<< same host as your pick
        reason: academic-tld,code:cs-143,univ
   2. [ score=19] https://web.stanford.edu/class/cs143/lectures/lecture01.pdf  <<<< same host as your pick
        reason: academic-tld,code:cs-143,kw:lecture,univ
   3. [   REJECT] https://www.scribd.com/document/791285516/Stanford-University-CS143-PPT
   4. [   REJECT] https://www.scribd.com/document/522615083/Slides-00
   5. [ score= 9] https://github.com/PKUFlyingPig/CS143-compiler
        reason: github,code:cs-143
   6. [ score=13] https://github.com/PKUFlyingPig/CS143-compiler/blob/master/ppt/lecture01-overview.pdf
        reason: github,code:cs-143,kw:lecture
   7. [   REJECT] https://csdiy.wiki/en/%E7%BC%96%E8%AF%91%E5%8E%9F%E7%90%86/CS143/
   8. [   REJECT] https://bobbyy.org/notes/cs143/
   9. [   REJECT] https://archive.org/details/academictorrents_e31e54905c7b2669c81fe164de2859be4697013a
  10. [   REJECT] https://www.cs61bbeyond.com/course/compilers/CS143

====================================================================================================
## [3] CS135 - University of Waterloo   (ratings=18, CS+>=4=YES)
- YOUR PICK : https://student.cs.uwaterloo.ca/~cs135/slides/
- MY READ   : dept student.cs host, ~<code>/slides/
- code variants looked for: ['cs-135', 'cs135', 'cs_135']  num=135

  YOUR REASON (type here): ______________________________________

  >> your pick's host appears at rank(s) [1, 2, 7], but the exact page is NOT in the top 20 -> you reached it by link manipulation (path/term edit).
----------------------------------------------------------------------------------------------------
   1. [ score=18] https://student.cs.uwaterloo.ca/~cs135/slides/index.html  <<<< same host as your pick
        reason: known-university,code:cs-135,kw:slide,univ,faculty
   2. [ score=21] https://student.cs.uwaterloo.ca/~cs135/w26-slides/CS135-W26-L00.pdf  <<<< same host as your pick
        reason: known-university,code:cs-135,kw:slide,term,univ,faculty
   3. [   REJECT] https://www.slideshare.net/slideshow/cs-135/237062653
   4. [   REJECT] https://www.studocu.com/en-ca/course/university-of-waterloo/designing-functional-programs/297547
   5. [   REJECT] https://www.studocu.com/en-ca/document/university-of-waterloo/designing-functional-programs/m01-syllabus-cs135-summary-designing-functional-programs/89289399
   6. [ score= 9] https://github.com/gotibhai/CS-135
        reason: github,code:cs-135
   7. [ score=14] https://student.cs.uwaterloo.ca/~cs135/  <<<< same host as your pick
        reason: known-university,code:cs-135,univ,faculty
   8. [   REJECT] https://www.studocu.com/en-ca/course/university-of-waterloo/designing-functional-programs/lecture-notes/297547/3
   9. [ score=14] https://wwwtest.student.cs.uwaterloo.ca/~cs135/smods/01-syllabus/
        reason: known-university,code:cs-135,univ,faculty
  10. [ score=13] https://cs.uwaterloo.ca/current/courses/course_descriptions/cDescr/CS135
        reason: known-university,code:cs-135,univ

====================================================================================================
## [4] CMSC250 - University of Maryland   (ratings=7, CS+>=4=YES)
- YOUR PICK : https://www.cs.umd.edu/~gasarch/COURSES/250/S26/slides.html
- MY READ   : cross-listed (Math dept) but lives on cs.umd.edu; ~prof + number + slides
- code variants looked for: ['cmsc-250', 'cmsc250', 'cmsc_250']  num=250

  YOUR REASON (type here): ______________________________________

  >> your pick's host appears at rank(s) [1], but the exact page is NOT in the top 20 -> you reached it by link manipulation (path/term edit).
----------------------------------------------------------------------------------------------------
   1. [ score=16] https://www.cs.umd.edu/class/fall2024/cmsc250-010X/  <<<< same host as your pick
        reason: academic-tld,code:cmsc-250,term
   2. [ score= 5] https://github.com/jasonfilippou/UMDCourseSlides
        reason: github,kw:slide
   3. [ score=14] https://bakalian.cs.umd.edu/fall22/250/slides
        reason: academic-tld,num:250,kw:slide,term
   4. [ score= 7] https://github.com/ebuka-o/UMD250and420CourseSlides
        reason: github,num:250,kw:slide
   5. [   REJECT] https://www.reddit.com/r/UMD/comments/11dwvzm/cmsc250_recorded_lectures/
   6. [   REJECT] https://www.coursehero.com/sitemap/schools/1035-University-of-Maryland-University-College/courses/9169478-CMSC250/
   7. [   REJECT] https://www.coursehero.com/sitemap/schools/74-University-of-Maryland/courses/528088-CMSC250/
   8. [   REJECT] https://planetterp.com/course/CMSC250
   9. [   REJECT] https://www.coursicle.com/umd/courses/CMSC/250/
  10. [   REJECT] https://www.coursesidekick.com/courses/university-of-maryland-college-park-cmsc-250-16022

====================================================================================================
## [5] COMP233 - Concordia University   (ratings=5, CS+>=4=YES)
- YOUR PICK : https://users.encs.concordia.ca/~doedel/courses/comp-233/slides.pdf
- MY READ   : hyphenated code comp-233, single combined slides.pdf (@@)
- code variants looked for: ['comp-233', 'comp233', 'comp_233']  num=233

  YOUR REASON (type here): ______________________________________

  >> your exact pick appeared at rank #1.
----------------------------------------------------------------------------------------------------
   1. [ score=18] https://users.encs.concordia.ca/~doedel/courses/comp-233/slides.pdf  <<<< YOUR EXACT PICK
        reason: known-university,code:comp_233,kw:slide,univ,faculty
   2. [   REJECT] https://www.studocu.com/en-ca/document/concordia-university/probability-and-statistics-for-computer-science/session-01-first-lecture-slides/105211461
   3. [   REJECT] https://www.coursehero.com/sitemap/schools/2809-Concordia-University/courses/1715634-COMP233/
   4. [   REJECT] https://hisham.bearblog.dev/comp233/
   5. [   REJECT] https://www.studocu.com/en-ca/course/concordia-university/probability-and-statistics-for-computer-science/1424337
   6. [   REJECT] https://concordia.courses/course/comp-233
   7. [   REJECT] https://www.studocu.com/en-ca/document/concordia-university/probability-and-statistics-for-computer-science/comp-233-course-outline-fall-2021/27090173
   8. [   REJECT] https://www.studocu.com/en-ca/document/concordia-university/probability-and-statistics-for-computer-science/comp-233-course-outline/12488099
   9. [ score=14] https://users.encs.concordia.ca/~doedel/courses/comp-233/course_outline.pdf  <<<< same host as your pick
        reason: known-university,code:comp_233,univ,faculty
  10. [   REJECT] https://www.studocu.com/en-ca/document/concordia-university/probability-and-statistics-for-computer-science/course-outline-comp-233-winter-2025/115345965

====================================================================================================
## [6] CSE167 - University of California, San Diego   (ratings=6, CS+>=4=YES)
- YOUR PICK : https://cseweb.ucsd.edu/classes/wi18/cse167-a/
- MY READ   : cseweb host, term wi18, code with section suffix cse167-a
- code variants looked for: ['cse-167', 'cse167', 'cse_167']  num=167

  YOUR REASON (type here): ______________________________________

  >> your pick's host appears at rank(s) [1, 2], but the exact page is NOT in the top 20 -> you reached it by link manipulation (path/term edit).
----------------------------------------------------------------------------------------------------
   1. [ score=17] https://cseweb.ucsd.edu/~alchern/teaching/cse167_wi25/  <<<< same host as your pick
        reason: academic-tld,code:cse167,term,faculty
   2. [ score=17] https://cseweb.ucsd.edu/~tzli/cse167/fa2023/  <<<< same host as your pick
        reason: academic-tld,code:cse167,term,faculty
   3. [   REJECT] https://www.coursehero.com/sitemap/schools/70-University-of-California-San-Diego/courses/451806-CSE167/
   4. [   REJECT] https://keepnotes.com/university-of-california-san-diego/cse167-computer-graphics
   5. [   REJECT] https://edubirdie.com/docs/university-of-california-san-diego/cse167-computer-graphics
   6. [ score=11] https://kylewang1999.github.io/learn_with_me/cse167
        reason: course-host,code:cse167
   7. [   REJECT] https://piazza.com/ucsd/winter2025/cse167_wi25_a00
   8. [   REJECT] https://www.docsity.com/en/docs/lecture-slides-on-vectors-and-matrices-socc-167/6119752/
   9. [   REJECT] https://www.coursicle.com/ucsd/courses/CSE/167/
  10. [   REJECT] https://www.edx.org/learn/computer-graphics/the-university-of-california-san-diego-computer-graphics

====================================================================================================
## [7] EECS6893 - Columbia University   (ratings=5, CS+>=4=YES)
- YOUR PICK : https://www.ee.columbia.edu/~cylin/course/bigdata/
- MY READ   : named course 'bigdata' (no code in URL); cross-listed on ee.columbia.edu
- code variants looked for: ['eecs-6893', 'eecs6893', 'eecs_6893']  num=6893

  YOUR REASON (type here): ______________________________________

  >> your exact pick appeared at rank #1.
----------------------------------------------------------------------------------------------------
   1. [ score= 8] https://www.ee.columbia.edu/~cylin/course/bigdata/  <<<< YOUR EXACT PICK
        reason: academic-tld,univ,faculty
   2. [ score= 9] https://github.com/Yutao-Zhou/EECS_6893_Big_Data_Analytics
        reason: github,code:eecs_6893
   3. [ score=20] https://www.ee.columbia.edu/~cylin/course/bigdata/EECS6893-BigDataAnalytics-Lecture1.pdf  <<<< same host as your pick
        reason: academic-tld,code:eecs_6893,kw:lecture,univ,faculty
   4. [   REJECT] https://www.slideshare.net/slideshow/eecs6893-big-dataanalyticslecture1/64832681
   5. [ score= 9] https://github.com/KZy1218/EECS6893_BigDataAnalytics
        reason: github,code:eecs_6893
   6. [   REJECT] https://www.coursehero.com/sitemap/schools/40-Columbia-University/courses/4357468-EECS6893/
   7. [   REJECT] https://www.scribd.com/document/992467310/EECS6893-BigDataAnalytics-Lecture1
   8. [   REJECT] https://www.scribd.com/document/379062766/EECS6893-BigDataAnalytics-Lecture1
   9. [   REJECT] https://www.coursehero.com/file/246613564/EECS6893-BigDataAnalytics-Lecture1-6pdf/
  10. [   REJECT] https://www.coursicle.com/columbia/courses/EECS/E6893/

====================================================================================================
## [8] CSE205 - Arizona State University   (ratings=63, CS+>=4=YES)
- YOUR PICK : http://www.javiergs.com/teaching/cse205/
- MY READ   : instructor personal .com site /teaching/<code>
- code variants looked for: ['cse-205', 'cse205', 'cse_205']  num=205

  YOUR REASON (type here): ______________________________________

  >> your exact pick appeared at rank #7.
----------------------------------------------------------------------------------------------------
   1. [ score= 5] https://courses.asuonline.asu.edu/object-oriented-programming-and-data-structures
        reason: academic-tld
   2. [   REJECT] https://www.coursehero.com/sitemap/schools/4361-Arizona-State-University/courses/304909-CSE205/
   3. [   REJECT] https://www.studocu.com/en-us/course/arizona-state-university/object-oriented-programming-and-data-structures/5916407
   4. [ score= 5] https://webapp4.asu.edu/bookstore/viewsyllabus/2231/31836/pdf
        reason: academic-tld
   5. [   REJECT] https://www.slideshare.net/slideshow/0-cse205-3ppt/253858995
   6. [   REJECT] https://www.studocu.com/en-us/document/arizona-state-university/object-oriented-programming-and-data-structures/lecture-week-1/44623321
   7. [   REJECT] http://www.javiergs.com/teaching/cse205/  <<<< YOUR EXACT PICK
   8. [   REJECT] https://www.sweetstudy.com/note-bank/arizona-state-university/cse-205-object-oriented-programming-and-data-structures
   9. [   REJECT] https://www.cliffsnotes.com/study-notes/13346975
  10. [   REJECT] https://www.sweetstudy.com/note-bank/arizona-state-university/cse-205-object-oriented-programming-and-data-structures/cse-205javafxanimationobject-orientedprogramminganddatastructures-ppt-pdf

====================================================================================================
## [9] CS122 - Carnegie Mellon University   (ratings=32, CS+>=4=YES)
- YOUR PICK : https://www.cs.cmu.edu/~15122/handouts.shtml
- MY READ   : CMU 15- numbering (CS122 -> 15122); handouts.shtml not 'slides'
- code variants looked for: ['cs-122', 'cs122', 'cs_122']  num=122

  YOUR REASON (type here): ______________________________________

  >> your exact pick appeared at rank #1.
----------------------------------------------------------------------------------------------------
   1. [ score=12] https://www.cs.cmu.edu/~15122/handouts.shtml  <<<< YOUR EXACT PICK
        reason: academic-tld,num:122,kw:handout,faculty
   2. [ score= 8] https://www.cs.cmu.edu/~15122/syllabus.shtml  <<<< same host as your pick
        reason: academic-tld,num:122,faculty
   3. [ score=12] https://www.cs.cmu.edu/~15122/schedule.shtml  <<<< same host as your pick
        reason: academic-tld,num:122,kw:schedule,faculty
   4. [ score=17] https://www.cs.cmu.edu/~iliano/courses/22S-CMU-CS122/syllabus.shtml  <<<< same host as your pick
        reason: academic-tld,code:cs_122,term,faculty
   5. [   REJECT] https://collegeclassreviews.com/universities/carnegie-mellon-university/courses/cs122
   6. [   REJECT] https://courses.scottylabs.org/course/15-122
   7. [   REJECT] http://coursecatalog.web.cmu.edu/
   8. [ score=17] https://www.cs.cmu.edu/~iliano/courses/20S-CMU-CS122/syllabus.shtml  <<<< same host as your pick
        reason: academic-tld,code:cs_122,term,faculty
   9. [   REJECT] http://coursecatalog.web.cmu.edu/coursedescriptions/
  10. [ score= 7] https://csd.cmu.edu/15122-principles-of-imperative-computation
        reason: academic-tld,num:122

====================================================================================================
## [10] CS4820 - Cornell University   (ratings=18, CS+>=4=YES)
- YOUR PICK : https://www.cs.cornell.edu/courses/cs4820/2024sp/lectures/
- MY READ   : cs.cornell.edu/courses/<code>/<term>/lectures/
- code variants looked for: ['cs-4820', 'cs4820', 'cs_4820']  num=4820

  YOUR REASON (type here): ______________________________________

  >> your pick's host appears at rank(s) [1], but the exact page is NOT in the top 20 -> you reached it by link manipulation (path/term edit).
----------------------------------------------------------------------------------------------------
   1. [ score=18] https://www.cs.cornell.edu/courses/cs4820/2025sp/  <<<< same host as your pick
        reason: academic-tld,code:cs-4820,term,univ
   2. [ score=13] https://cornellcswiki.gitlab.io/classes/CS4820.html
        reason: course-host,code:cs-4820,univ
   3. [   REJECT] https://www.studocu.com/en-us/course/cornell-university/introduction-to-analysis-of-algorithms/3918536
   4. [ score=13] https://cornellphysicswiki.github.io/classes/cs/CS4820.html
        reason: course-host,code:cs-4820,univ
   5. [ score= 9] https://github.com/CornellCSWiki/CornellCSWiki/blob/master/classes/CS4820.md
        reason: github,code:cs-4820
   6. [   REJECT] https://www.coursehero.com/sitemap/schools/11-Cornell-University/courses/82055-CS4820/
   7. [   REJECT] https://dantasfiles.com/2025/03/28/notes-on-cornell-cs-4820.html
   8. [ score=18] https://courses.cs.cornell.edu/cs4820/2025fa/syllabus/
        reason: academic-tld,code:cs-4820,term,univ
   9. [ score=18] https://courses.cs.cornell.edu/cs4820/2023sp/syllabus/
        reason: academic-tld,code:cs-4820,term,univ
  10. [ score=22] https://courses.cs.cornell.edu/cs4820/2025fa/lectures/
        reason: academic-tld,code:cs-4820,kw:lecture,term,univ

====================================================================================================
## [11] CS3510 - Georgia Institute of Technology   (ratings=145, CS+>=4=YES)
- YOUR PICK : https://www.cs3510.com/lectures/
- MY READ   : course has its own vanity .com domain /lectures/
- code variants looked for: ['cs-3510', 'cs3510', 'cs_3510']  num=3510

  YOUR REASON (type here): ______________________________________

  >> your pick's host appears at rank(s) [2], but the exact page is NOT in the top 20 -> you reached it by link manipulation (path/term edit).
----------------------------------------------------------------------------------------------------
   1. [   REJECT] http://summer2023.cs3510.com/
   2. [   REJECT] http://www.cs3510.com/  <<<< same host as your pick
   3. [ score=16] https://syllabus.gatech.edu/sites/default/files/2026-04/cs3510syllabus_0.pdf
        reason: academic-tld,code:cs3510,term
   4. [ score=11] https://gt-cs-3510.github.io/
        reason: course-host,code:cs3510
   5. [ score=11] https://faculty.cc.gatech.edu/~ladha/S26/3510/
        reason: academic-tld,num:3510,term,faculty
   6. [   REJECT] https://www.coursesidekick.com/courses/georgia-institute-of-technology-cs-3510-20513
   7. [   REJECT] https://www.studocu.com/en-us/course/georgia-institute-of-technology/dsgnanalysis-algorithms/526732
   8. [   REJECT] https://www.studocu.com/en-us/document/georgia-institute-of-technology/dsgnanalysis-algorithms/dsgnanalysis-algorithms-cs-3510-c/89947133
   9. [ score= 9] https://github.com/markov42/CS3510
        reason: github,code:cs3510
  10. [   REJECT] https://www.coursehero.com/sitemap/schools/47-Georgia-Institute-Of-Technology/courses/519679-CS3510/

====================================================================================================
## [12] CSE232 - Michigan State University   (ratings=193, CS+>=4=YES)
- YOUR PICK : https://cse.msu.edu/~cse232/us22/lectures.html
- MY READ   : cse.msu.edu/~<code>/<term>/lectures.html
- code variants looked for: ['cse-232', 'cse232', 'cse_232']  num=232

  YOUR REASON (type here): ______________________________________

  >> your pick's host appears at rank(s) [2, 3], but the exact page is NOT in the top 20 -> you reached it by link manipulation (path/term edit).
----------------------------------------------------------------------------------------------------
   1. [ score=11] https://cse232-msu.github.io/CSE232/
        reason: course-host,code:cse232
   2. [ score=18] https://cse.msu.edu/~cse232/us22/slides/cse232_us22_introduction.pdf  <<<< same host as your pick
        reason: academic-tld,code:cse232,kw:slide,faculty
   3. [ score=14] https://www.cse.msu.edu/~cse232/  <<<< same host as your pick
        reason: academic-tld,code:cse232,faculty
   4. [ score=11] https://cse232-msu.github.io/CSE232/syllabus.html
        reason: course-host,code:cse232
   5. [ score= 9] https://github.com/CSE232-MSU/CSE232/tree/main/
        reason: github,code:cse232
   6. [   REJECT] https://www.studocu.com/en-us/course/michigan-state-university/introduction-to-programming-ii/868860
   7. [ score= 9] https://github.com/CSE232-MSU/CSE232
        reason: github,code:cse232
   8. [   REJECT] https://www.coursehero.com/sitemap/schools/2600-Michigan-State-University/courses/208502-CSE232/
   9. [   REJECT] https://www.coursicle.com/msu/courses/CSE/232/
  10. [   REJECT] https://www.studocu.com/en-us/document/michigan-state-university/introduction-to-programming-ii/3cef0bd88cf5c57529-dba8223c1d8890-object-oriented-design-course-notes/66202294

