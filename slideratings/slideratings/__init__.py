"""slideratings — CCR-driven course-ratings + lecture-slide scraper.

Pipeline: rank universities on CollegeClassReviews by number of rated courses,
then for every rated course collect CCR metrics, look up Rate My Professors
ratings, and discover + download lecture slides into a RateMySlides-style
``data/{uni}-{coursenum}/{N}.pdf`` layout. Everything is recorded in SQLite.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
