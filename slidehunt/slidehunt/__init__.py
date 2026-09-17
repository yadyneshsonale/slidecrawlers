"""slidehunt — random-code course-slide scraper.

Generates random STEM/CS course codes, web-searches "<code> course slides",
downloads + verifies the decks it finds (skipping MIT and anything already
present in slidefetch/downloads), then infers the university from the download
host and the instructor(s) from the page to fetch Rate My Professors and
CollegeClassReviews ratings. All metadata is stored in a separate SQLite DB.
"""

__version__ = "0.1.0"
