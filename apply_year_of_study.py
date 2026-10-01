"""
Adds Year of Study to the student registration in app.py.

Usage (run in the folder that contains your app.py):

    python apply_year_of_study.py

It writes the result to app_updated.py (your app.py is NOT touched).
Every edit is checked to match exactly once; if anything does not
match, the script stops and tells you which step failed.
"""

import re
import sys

SRC = "app.py"
DST = "app_updated.py"

with open(SRC, "r", encoding="utf-8") as f:
    code = f.read()


def apply(step, pattern, replacement, flags=0):
    global code
    new_code, count = re.subn(pattern, replacement, code, flags=flags)
    if count != 1:
        sys.exit(f"[FAILED] {step}: expected 1 match, found {count}. "
                 "Nothing was written.")
    code = new_code
    print(f"[ok] {step}")


# 1. Read the field in the student branch of register()
apply(
    "read year_of_study",
    r'(            academic_year = request\.form\.get\("academic_year", ""\)\.strip\(\)\n)'
    r'(\n            if not uucms_number:)',
    r'\1            year_of_study = request.form.get("year_of_study", "").strip()\n\2',
)

# 2. Validate it after the Academic Year check
apply(
    "validate year_of_study",
    r'(            if not academic_year:\n'
    r'                flash\("Please enter Academic Year\."\)\n'
    r'                return redirect\(url_for\("register"\)\)\n)',
    r'\1\n'
    r'            if year_of_study not in ("1", "2", "3", "4"):\n'
    r'                flash("Please select your Year of Study.")\n'
    r'                return redirect(url_for("register"))\n',
)

# 3. Student INSERT: column, placeholder and value
apply(
    "student INSERT",
    r'(INSERT INTO student\s*\(\s*uucms_number,\s*name,\s*email,\s*password,'
    r'\s*course_id,\s*section_id,\s*academic_year)'
    r'(\s*\)\s*VALUES\s*\(%s, %s, %s, %s, %s, %s, %s)\)'
    r'(\s*""",\s*\(\s*uucms_number,\s*student_name,\s*student_email,'
    r'\s*password_hash,\s*course_id,\s*section_id,\s*academic_year)'
    r'(\s*\))',
    r'\1,\n                    year_of_study\2, %s)\3,\n'
    r'                    int(year_of_study)\4',
)

# 4. Schema helper (placed just before the OTP routes)
SCHEMA_FUNC = '''def ensure_student_schema():
    """Adds student.year_of_study if it doesn't exist yet."""
    connection = get_connection()

    if connection is None:
        return

    cursor = connection.cursor()

    try:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'student' AND COLUMN_NAME = 'year_of_study'"
        )

        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "ALTER TABLE student ADD COLUMN year_of_study TINYINT NULL"
            )

        connection.commit()

    except Exception as e:
        connection.rollback()
        print("Student schema error:", e)

    finally:
        cursor.close()
        connection.close()


'''

apply(
    "add ensure_student_schema()",
    r'(@app\.route\("/otp/send", methods=\["POST"\]\))',
    lambda m: SCHEMA_FUNC + m.group(1),
)

# 5. Call it at startup
apply(
    "call ensure_student_schema() at startup",
    r'(?m)^ensure_otp_schema\(\)$',
    "ensure_otp_schema()\nensure_student_schema()",
)

with open(DST, "w", encoding="utf-8") as f:
    f.write(code)

print(f"\nDone. Wrote {DST}. Review it, then replace app.py with it.")