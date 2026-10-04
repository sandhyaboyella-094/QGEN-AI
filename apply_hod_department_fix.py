"""
Usage:   python apply_hod_department_fix.py app.py
Output:  app_updated.py  (your original app.py is NOT modified)

Every edit must match exactly once. If any anchor is not found, the script
stops and writes nothing, so you never get a half-patched file.
"""
import sys

src_path = sys.argv[1] if len(sys.argv) > 1 else "app_ai.py"

with open(src_path, "r", encoding="utf-8", newline="") as f:
    text = f.read()

crlf = "\r\n" in text
if crlf:
    text = text.replace("\r\n", "\n")

EDITS = []


def edit(name, old, new):
    EDITS.append((name, old, new))


# ---------------------------------------------------------------
# 1. Helpers: subject.department column + HOD department guard
# ---------------------------------------------------------------
edit("helpers", '''def hod_required():
    return session.get("role") == "hod"
''', '''def hod_required():
    return session.get("role") == "hod"


def ensure_subject_schema():
    """Adds subject.department if it doesn't exist yet."""
    connection = get_connection()

    if connection is None:
        return

    cursor = connection.cursor()

    try:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'subject' AND COLUMN_NAME = 'department'"
        )

        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "ALTER TABLE subject ADD COLUMN department VARCHAR(100) NULL"
            )

        connection.commit()

        cursor.execute(
            "SELECT COUNT(*) FROM subject "
            "WHERE department IS NULL OR department = ''"
        )
        missing = cursor.fetchone()[0]

        if missing:
            print(
                f"[WARNING] {missing} subject row(s) have no department and "
                "will be hidden from every HOD. Run, for example:\\n"
                "  UPDATE subject SET department = 'bsc' WHERE course_id = 'BCA';"
            )

    except Exception as e:
        connection.rollback()
        print("Subject schema error:", e)

    finally:
        cursor.close()
        connection.close()


@app.before_request
def require_hod_department():
    """
    Every /hod/ page needs a department in the session. Without it the
    old code fell back to showing ALL departments, so block instead.
    """
    if request.path.startswith("/hod/") and hod_required():
        if not (session.get("department") or "").strip():
            session.clear()
            flash("Your department is not set. Please contact the administrator.")
            return redirect(url_for("faculty_login"))
''')

edit("startup call", '''ensure_otp_schema()
ensure_student_schema()
''', '''ensure_otp_schema()
ensure_student_schema()
ensure_subject_schema()
''')

# ---------------------------------------------------------------
# 2. Dashboard: subject count
# ---------------------------------------------------------------
edit("dashboard subject count", '''            SELECT COUNT(*) AS total
            FROM subject
            """
        )''', '''            SELECT COUNT(*) AS total
            FROM subject
            WHERE LOWER(department) = LOWER(%s)
            """,
            (hod_department,)
        )''')

# ---------------------------------------------------------------
# 3. Manage Subjects: list
# ---------------------------------------------------------------
edit("hod_subjects list", '''                see_duration
            FROM subject
            ORDER BY course_id, subject_id
            """
        )
        subjects = cursor.fetchall()''', '''                see_duration
            FROM subject
            WHERE LOWER(department) = LOWER(%s)
            ORDER BY course_id, subject_id
            """,
            (session.get("department", ""),)
        )
        subjects = cursor.fetchall()''')

# ---------------------------------------------------------------
# 4. Add subject: save the HOD's department (from session, not form)
# ---------------------------------------------------------------
edit("add_subject columns", '''                see_duration
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )''', '''                see_duration,
                department
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s
            )''')

edit("add_subject params", '''                see_pattern,
                see_duration
            )
        )

        connection.commit()

        flash("Subject added successfully.", "success")''', '''                see_pattern,
                see_duration,
                session.get("department", "")
            )
        )

        connection.commit()

        flash("Subject added successfully.", "success")''')

# ---------------------------------------------------------------
# 5. Edit subject: only own department
# ---------------------------------------------------------------
edit("edit_subject update where", '''                WHERE subject_id = %s
                """,
                (
                    course_id,''', '''                WHERE subject_id = %s
                AND LOWER(department) = LOWER(%s)
                """,
                (
                    course_id,''')

edit("edit_subject update params", '''                    see_duration,
                    subject_id
                )''', '''                    see_duration,
                    subject_id,
                    session.get("department", "")
                )''')

edit("edit_subject load", '''            FROM subject
            WHERE subject_id = %s
            """,
            (subject_id,)
        )

        subject = cursor.fetchone()

    except Exception as e:
        print("Load subject error:", e)''', '''            FROM subject
            WHERE subject_id = %s
            AND LOWER(department) = LOWER(%s)
            """,
            (subject_id, session.get("department", ""))
        )

        subject = cursor.fetchone()

    except Exception as e:
        print("Load subject error:", e)''')

# ---------------------------------------------------------------
# 6. Delete subject: only own department
# ---------------------------------------------------------------
edit("delete_subject check", '''            WHERE subject_id = %s
            """,
            (subject_id,)
        )

        subject = cursor.fetchone()

        if not subject:''', '''            WHERE subject_id = %s
            AND LOWER(department) = LOWER(%s)
            """,
            (subject_id, session.get("department", ""))
        )

        subject = cursor.fetchone()

        if not subject:''')

edit("delete_subject delete", '''            DELETE FROM subject
            WHERE subject_id = %s
            """,
            (subject_id,)''', '''            DELETE FROM subject
            WHERE subject_id = %s
            AND LOWER(department) = LOWER(%s)
            """,
            (subject_id, session.get("department", ""))''')

# ---------------------------------------------------------------
# 7. Assign subject: dropdown + POST validation
# ---------------------------------------------------------------
edit("assign dropdown subjects", '''            FROM subject
            ORDER BY course_id, subject_id
            """
        )

        subjects = cursor.fetchall()''', '''            FROM subject
            WHERE LOWER(department) = LOWER(%s)
            ORDER BY course_id, subject_id
            """,
            (hod_department or "",)
        )

        subjects = cursor.fetchall()''')

edit("assign POST subject lookup", '''                FROM subject
                WHERE subject_id = %s
                """,
                (subject_id,)
            )

            subject = cursor.fetchone()''', '''                FROM subject
                WHERE subject_id = %s
                AND LOWER(department) = LOWER(%s)
                """,
                (subject_id, hod_department or "")
            )

            subject = cursor.fetchone()''')

# ---------------------------------------------------------------
# 8. Syllabus view / download by ID: own department only
# ---------------------------------------------------------------
edit("hod view syllabus", '''            FROM syllabi
            WHERE id = %s
            """,
            (syllabus_id,)
        )

        syllabus = cursor.fetchone()

    except Exception as e:
        print("View syllabus error:", e)''', '''            FROM syllabi s
            INNER JOIN faculty f
                ON s.faculty_id = f.faculty_id
            WHERE s.id = %s
            AND f.department = %s
            """,
            (syllabus_id, session.get("department", ""))
        )

        syllabus = cursor.fetchone()

    except Exception as e:
        print("View syllabus error:", e)''')

edit("hod download syllabus", '''            FROM syllabi
            WHERE id = %s
            """,
            (syllabus_id,)
        )

        syllabus = cursor.fetchone()

        if syllabus:''', '''            FROM syllabi s
            INNER JOIN faculty f
                ON s.faculty_id = f.faculty_id
            WHERE s.id = %s
            AND f.department = %s
            """,
            (syllabus_id, session.get("department", ""))
        )

        syllabus = cursor.fetchone()

        if syllabus:''')

# ---------------------------------------------------------------
# 9. Question paper view / download by ID: own department only
# ---------------------------------------------------------------
edit("hod view question paper", '''            FROM question_papers qp
            LEFT JOIN faculty f
                ON qp.faculty_id = f.faculty_id
            WHERE qp.id = %s
            """,
            (paper_id,)''', '''            FROM question_papers qp
            INNER JOIN faculty f
                ON qp.faculty_id = f.faculty_id
            WHERE qp.id = %s
            AND f.department = %s
            """,
            (paper_id, session.get("department", ""))''')

edit("hod download question paper", '''            SELECT
                id,
                questions
            FROM question_papers
            WHERE id = %s
            """,
            (paper_id,)''', '''            SELECT
                qp.id,
                qp.questions
            FROM question_papers qp
            INNER JOIN faculty f
                ON qp.faculty_id = f.faculty_id
            WHERE qp.id = %s
            AND f.department = %s
            """,
            (paper_id, session.get("department", ""))''')

# ---------------------------------------------------------------
# Apply
# ---------------------------------------------------------------
failed = False

for name, old, new in EDITS:
    count = text.count(old)
    if count != 1:
        print(f"[FAILED] {name}: anchor found {count} times (expected 1)")
        failed = True

if failed:
    print("\nNothing was written. Your app.py may differ from the version I saw "
          "(extra spaces, different indentation,"
          " or already edited).")
    sys.exit(1)

for name, old, new in EDITS:
    text = text.replace(old, new, 1)
    print(f"[OK] {name}")

if crlf:
    text = text.replace("\n", "\r\n")

out_path = "app_updated.py"

with open(out_path, "w", encoding="utf-8", newline="") as f:
    f.write(text)

print(f"\nDone. Wrote {out_path}. Review it, then replace app.py with it.")