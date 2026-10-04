"""
course_normalizer.py

Maps any way of typing a course name to ONE canonical course code.

    "BCA", "bca", "b.c.a", "Computer Applications",
    "Department of Computer Applications"          ->  "BCA"

    "bcom", "B.Com", "Bachelor of Commerce",
    "Department of Commerce"                       ->  "BCOM"

IMPORTANT: the canonical codes (BCA, BCOM, BSC ...) must match the values
stored in subject.course_id, because the Generate Paper page matches
subjects by course_id.
"""

import re
from difflib import get_close_matches


# ---------------------------------------------------------------
# 1. CANONICAL COURSES AND THEIR ALIASES
#    Add or edit entries here. Aliases are matched after cleaning
#    (lower-case, no dots/punctuation, no "department of" etc.).
# ---------------------------------------------------------------

COURSE_ALIASES = {
    "BCA": [
        "bca",
        "bachelor of computer applications",
        "bachelor of computer application",
        "computer applications",
        "computer application",
    ],
    "MCA": [
        "mca",
        "master of computer applications",
        "master of computer application",
    ],
    "BCOM": [
        "bcom",
        "bachelor of commerce",
        "commerce",
    ],
    "MCOM": [
        "mcom",
        "master of commerce",
    ],
    "BSC": [
        "bsc",
        "bachelor of science",
        "science",
    ],
    "MSC": [
        "msc",
        "master of science",
    ],
    "BBA": [
        "bba",
        "bachelor of business administration",
        "business administration",
    ],
    "MBA": [
        "mba",
        "master of business administration",
    ],
    "BA": [
        "ba",
        "bachelor of arts",
        "arts",
    ],
    "MA": [
        "ma",
        "master of arts",
    ],
}


# ---------------------------------------------------------------
# 2. TEXT CLEANING
# ---------------------------------------------------------------

_PREFIX_WORDS = r"^(department of|dept of|dept|department|school of|faculty of)\s+"
_SUFFIX_WORDS = r"\s+(department|dept|course|courses|degree|program|programme|class)$"


def _clean(text):
    """lower-case, drop punctuation, remove 'department of' style words."""
    if not text:
        return ""

    text = text.lower().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", "", text)      # b.c.a -> bca , b.com -> bcom
    text = re.sub(r"\s+", " ", text).strip()

    text = re.sub(_PREFIX_WORDS, "", text)
    text = re.sub(_SUFFIX_WORDS, "", text)

    return text.strip()


def _compact(text):
    """'b c a' -> 'bca' so spaced-out abbreviations still match."""
    return text.replace(" ", "")


# ---------------------------------------------------------------
# 3. LOOKUP TABLES (built once at import time)
# ---------------------------------------------------------------

_ALIAS_TO_CODE = {}

for _code, _aliases in COURSE_ALIASES.items():
    _ALIAS_TO_CODE[_clean(_code)] = _code
    _ALIAS_TO_CODE[_compact(_clean(_code))] = _code

    for _alias in _aliases:
        cleaned = _clean(_alias)
        _ALIAS_TO_CODE[cleaned] = _code
        _ALIAS_TO_CODE[_compact(cleaned)] = _code


# ---------------------------------------------------------------
# 4. PUBLIC FUNCTIONS
# ---------------------------------------------------------------

def normalize_course(raw, fuzzy=True):
    """
    Returns the canonical course code (e.g. "BCA"), or None if the
    input cannot be matched to any known course.

    fuzzy=True also accepts small typos ("computr applications").
    """
    cleaned = _clean(raw)

    if not cleaned:
        return None

    # exact match (with and without spaces)
    if cleaned in _ALIAS_TO_CODE:
        return _ALIAS_TO_CODE[cleaned]

    if _compact(cleaned) in _ALIAS_TO_CODE:
        return _ALIAS_TO_CODE[_compact(cleaned)]

    # typo tolerance - only for longer inputs, so short strings
    # like "bc" are never guessed
    if fuzzy and len(cleaned) >= 6:
        matches = get_close_matches(
            cleaned,
            list(_ALIAS_TO_CODE.keys()),
            n=1,
            cutoff=0.85
        )

        if matches:
            return _ALIAS_TO_CODE[matches[0]]

    return None


def valid_course_codes():
    """List of canonical codes, e.g. for dropdowns and error messages."""
    return list(COURSE_ALIASES.keys())


# ---------------------------------------------------------------
# 5. HOW TO USE IN register()   (student branch)
# ---------------------------------------------------------------
#
#   from course_normalizer import normalize_course, valid_course_codes
#
#   course_id = normalize_course(request.form.get("course_id", ""))
#
#   if not course_id:
#       flash(
#           "Course not recognised. Use one of: "
#           + ", ".join(valid_course_codes()),
#           "error"
#       )
#       return redirect(url_for("register"))
#
#   # save course_id (the canonical code) in the student INSERT
#
# Also normalise anywhere else a course is typed in: faculty subject
# assignment, admin roster upload, subject creation, etc.


# ---------------------------------------------------------------
# 6. ONE-TIME CLEANUP OF EXISTING STUDENT ROWS
#    Run once (e.g. from a Flask shell) after adding this file.
#    Adjust table/column names to match your schema.
# ---------------------------------------------------------------

def normalize_existing_students(connection):
    cursor = connection.cursor(dictionary=True)
    updated, unmatched = 0, []

    try:
        cursor.execute("SELECT uucms_number, course_id FROM student")

        for row in cursor.fetchall():
            code = normalize_course(row["course_id"])

            if not code:
                unmatched.append((row["uucms_number"], row["course_id"]))
                continue

            if code != row["course_id"]:
                cursor.execute(
                    "UPDATE student SET course_id = %s WHERE uucms_number = %s",
                    (code, row["uucms_number"])
                )
                updated += 1

        connection.commit()

    finally:
        cursor.close()

    return updated, unmatched
