PART_HEADING_CSS = """
.part-heading {
    text-align: center;
    font-weight: bold;
    font-style: italic;
    text-decoration: underline;
    margin: 18px 0 12px 0;
}
"""

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory,
    send_file,
    jsonify,
    abort
)

import os
import secrets
import json
import random
import re
import itertools
import hmac
import smtplib
from email.message import EmailMessage
import hashlib
import time
import requests
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from werkzeug.utils import secure_filename
from course_normalizer import normalize_course, valid_course_codes
from datetime import date

def get_current_academic_year():
    t = date.today()
    start = t.year if t.month >= 6 else t.year - 1
    return f"{start}-{start + 1}"

from pypdf import PdfReader

from database import (
    get_connection,
    initialize_database
)

# =========================================================
# APP SETUP
# =========================================================

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def load_secret_key():
    """
    Use SECRET_KEY from .env when present. Otherwise create a random key
    once and keep it in a local file, so sessions and OTP hashes survive
    a server restart.
    """
    env_key = os.environ.get("SECRET_KEY")

    if env_key:
        return env_key

    key_path = os.path.join(BASE_DIR, ".secret_key")

    try:
        if os.path.exists(key_path):
            with open(key_path, "r", encoding="utf-8") as key_file:
                stored = key_file.read().strip()
            if stored:
                return stored

        generated = secrets.token_hex(32)

        with open(key_path, "w", encoding="utf-8") as key_file:
            key_file.write(generated)

        return generated

    except OSError as e:
        print("Secret key file error:", e)
        return secrets.token_hex(32)


app.secret_key = load_secret_key()

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "syllabi")
PROFILE_UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "profiles")

ALLOWED_EXTENSIONS = {"pdf"}
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROFILE_UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["PROFILE_UPLOAD_FOLDER"] = PROFILE_UPLOAD_FOLDER




@app.context_processor
def inject_faculty_theme():

    theme = 'dark'

    if 'faculty_id' in session:

        connection = get_connection()

        if connection:
            cursor = connection.cursor(dictionary=True)

            cursor.execute("""
                SELECT theme
                FROM faculty_settings
                WHERE faculty_id = %s
            """, (session['faculty_id'],))

            result = cursor.fetchone()

            cursor.close()
            connection.close()

            if result and result.get('theme') in ['dark', 'light']:
                theme = result['theme']

    return {
        'faculty_theme': theme
    }


# =========================================================
# BLOOM'S TAXONOMY QUESTION FORMATION TEMPLATES
# =========================================================

def format_part_heading(part_label):
    """Return a centered, bold, italic and underlined Part A/B/C heading."""
    safe_label = str(part_label).strip()
    return (
        '<div class="part-heading">'
        f'<strong><em><u>{safe_label}</u></em></strong>'
        '</div>'
    )


def build_subquestion_marks(total_marks):
    total_marks = int(total_marks)
    distributions = {2: [1, 1], 5: [2, 3], 10: [5, 5]}
    if total_marks not in distributions:
        raise ValueError("Mixed pattern supports only 2, 5, and 10 marks.")
    return distributions[total_marks]


BLOOMS_QUESTION_TEMPLATES = {
    "remembering": [
        "What is the standard definition of {topic}?",
        "List the key components or characteristics of {topic}.",
        "State the primary purpose or function of {topic}.",
        "State the fundamental rule or principle that governs {topic}.",
        "Who is credited with developing {topic}, and in what context?",
        "Define {topic} and give one real-world example of its use."
    ],
    "understanding": [
        "Explain, in your own words, how {topic} works.",
        "Summarize the main idea behind {topic}.",
        "Describe the key characteristics that distinguish {topic} from related techniques.",
        "Illustrate {topic} with a simple example.",
        "Explain why {topic} is important in practice.",
        "Describe the typical steps involved in {topic}."
    ],
    "applying": [
        "Demonstrate, with a worked example, how {topic} can be applied to solve a practical problem.",
        "Show step by step how you would implement {topic} in a real project.",
        "Apply the principles of {topic} to a small example of your choice and explain the outcome.",
        "Describe how {topic} would be used to address a common challenge in this domain.",
        "Illustrate, with an example, how {topic} can be used to improve a system's performance.",
        "Walk through the process of using {topic} to complete a given task."
    ],
    "analyzing": [
        "Break down {topic} into its key components and explain how they interact.",
        "Compare {topic} with a related technique, highlighting their key differences.",
        "Analyze the strengths and limitations of {topic}.",
        "Examine the assumptions that {topic} relies on, and discuss when they may not hold.",
        "Identify the situations where {topic} is most effective, and explain why.",
        "Break down the potential failure modes of {topic} under real-world conditions."
    ],
    "evaluating": [
        "Critically evaluate the effectiveness of {topic} compared to alternative approaches.",
        "Justify why {topic} is widely used despite its known limitations.",
        "Assess the long-term impact of adopting {topic} in a real-world system.",
        "Evaluate the trade-offs involved in using {topic}.",
        "Defend or critique the choice of {topic} for a large-scale application.",
        "Rank the key risks associated with {topic}, from most to least critical, with justification."
    ],
    "creating": [
        "Design an improved version of {topic} that addresses one of its known limitations.",
        "Propose a new approach, inspired by {topic}, that could improve on it.",
        "Devise a plan to integrate {topic} into an existing system.",
        "Construct a small example that demonstrates the key idea behind {topic}.",
        "Develop a set of test cases to validate an implementation of {topic}.",
        "Design a specification for a system component based on {topic}."
    ]
}

# Curated, mark-appropriate templates for Mixed papers (template mode).
# Every sentence reads naturally for any syllabus topic name.
MIXED_TEMPLATES = {
    "Part A": {
        "Remember": [
            "What is {topic}?",
            "Define {topic}.",
            "State the purpose of {topic}.",
            "List the key characteristics of {topic}.",
            "Mention any two applications of {topic}.",
            "State any two advantages of {topic}.",
            "Write a short note on {topic}.",
        ],
        "Understand": [
            "Explain {topic} in brief.",
            "Why is {topic} important?",
            "Give one example of {topic}.",
            "Describe {topic} briefly.",
            "Summarize the main idea of {topic}.",
            "What problem does {topic} solve?",
        ],
    },
    "Part B": {
        "Understand": [
            "Explain {topic} with a suitable example.",
            "Describe the working of {topic}.",
            "Discuss the importance of {topic}.",
            "Explain the key steps involved in {topic}.",
            "Write a note on {topic}, highlighting its main features.",
        ],
        "Apply": [
            "Illustrate the use of {topic} with a worked example.",
            "Explain how {topic} can be applied to solve a practical problem.",
            "Demonstrate {topic} with a small example of your choice.",
            "Explain how {topic} is used in a real-world application.",
        ],
        "Analyze": [
            "Discuss the advantages and limitations of {topic}.",
            "Explain the significance of {topic} with suitable examples.",
            "Explain the key components of {topic} and how they interact.",
            "Discuss the situations where {topic} works well and where it does not.",
        ],
    },
    "Part C": {
        "Understand": [
            "Explain {topic} in detail with suitable examples.",
            "Describe {topic} in detail, covering its main steps and applications.",
            "Discuss the concept of {topic} with the help of a neat diagram or example.",
        ],
        "Apply": [
            "Explain, with a worked example, how {topic} is applied to solve a practical problem.",
            "Illustrate {topic} step by step on a small data set or scenario of your choice.",
            "Describe how {topic} would be used in a real project, with an example.",
        ],
        "Analyze": [
            "Analyze the strengths and limitations of {topic} with suitable examples.",
            "Examine the assumptions behind {topic} and discuss when they may not hold.",
            "Discuss the significance of {topic}, with suitable examples and applications.",
        ],
        "Evaluate": [
            "Critically evaluate {topic}, discussing its advantages, limitations and alternatives.",
            "Discuss the trade-offs involved in using {topic}.",
            "Justify the use of {topic} in practice, and discuss its drawbacks.",
        ],
        "Create": [
            "Design a small solution or workflow that demonstrates {topic}, and explain your design choices.",
            "Propose a plan to integrate {topic} into a real application, explaining each step.",
            "Develop an outline of how {topic} could be implemented and tested.",
        ],
    },
}

PROBLEM_TEMPLATES = [
    "Solve a numerical problem based on {topic}, showing every step of your working.",
    "Work through a suitable numerical example that applies {topic}.",
    "Given appropriate sample values, compute the result for {topic} step by step.",
    "Solve a problem that requires applying {topic}, and show your calculations clearly."
]

mcq_templates = [
    "Which of the following best describes {topic}?",
    "What is the primary purpose of {topic}?",
    "Which statement is correct regarding {topic}?",
    "Which of the following is most closely associated with {topic}?",
    "Which option represents an important characteristic of {topic}?",
    "Which of the following is a key feature of {topic}?",
    "Which of the following correctly explains {topic}?",
    "Which option correctly identifies the main function of {topic}?",
    "Which of the following represents an application of {topic}?",
    "Which statement best explains the role of {topic}?"
]

remember_templates = BLOOMS_QUESTION_TEMPLATES["remembering"]
understand_templates = BLOOMS_QUESTION_TEMPLATES["understanding"]
apply_templates = BLOOMS_QUESTION_TEMPLATES["applying"]
analyze_templates = BLOOMS_QUESTION_TEMPLATES["analyzing"]
evaluate_templates = BLOOMS_QUESTION_TEMPLATES["evaluating"]
create_templates = BLOOMS_QUESTION_TEMPLATES["creating"]
medium_pattern_templates = apply_templates

BLOOM_TEMPLATES = {
    "Remember": remember_templates,
    "Understand": understand_templates,
    "Apply": apply_templates,
    "Analyze": analyze_templates,
    "Evaluate": evaluate_templates,
    "Create": create_templates,
    "MCQ": mcq_templates
}


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def get_exam_duration(total_marks):
    try:
        total_marks = int(total_marks or 0)
    except (TypeError, ValueError):
        total_marks = 0

    if total_marks == 80:
        return "3 Hours"
    if total_marks <= 50:
        return "2 Hours"
    if total_marks >= 100:
        return "3 Hours"
    return "2 Hours 30 Minutes"


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def allowed_image_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS
    )


# =========================================================
# LOGIN CHECKS
# =========================================================

def admin_required():
    return session.get("role") == "admin"


def faculty_required():
    return session.get("role") == "faculty"


def hod_required():
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
                "will be hidden from every HOD. Run, for example:\n"
                "  UPDATE subject SET department = 'bsc' WHERE course_id = 'BCA';"
            )

    except Exception as e:
        connection.rollback()
        print("Subject schema error:", e)

    finally:
        cursor.close()
        connection.close()

def ensure_question_paper_schema():
    """Adds question_papers.academic_year and backfills old rows."""
    connection = get_connection()

    if connection is None:
        return

    cursor = connection.cursor()

    try:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'question_papers' "
            "AND COLUMN_NAME = 'academic_year'"
        )

        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "ALTER TABLE question_papers "
                "ADD COLUMN academic_year VARCHAR(9) NULL"
            )

        cursor.execute(
            """
            UPDATE question_papers
            SET academic_year = CASE
                WHEN MONTH(created_at) >= 6
                    THEN CONCAT(YEAR(created_at), '-', YEAR(created_at) + 1)
                ELSE CONCAT(YEAR(created_at) - 1, '-', YEAR(created_at))
            END
            WHERE academic_year IS NULL
            """
        )

        connection.commit()

    except Exception as e:
        connection.rollback()
        print("Question paper schema error:", e)

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



def ensure_semester_schema():
    """Adds subject.semester and student.semester if they don't exist yet."""
    connection = get_connection()

    if connection is None:
        return

    cursor = connection.cursor()

    try:
        for table in ("subject", "student"):
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = %s AND COLUMN_NAME = 'semester'",
                (table,)
            )

            if cursor.fetchone()[0] == 0:
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN semester TINYINT NULL"
                )

        connection.commit()

    except Exception as e:
        connection.rollback()
        print("Semester schema error:", e)

    finally:
        cursor.close()
        connection.close()        


# =========================================================
# EMAIL OTP VERIFICATION
# =========================================================

OTP_TTL_MIN = 5
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_SECONDS = 60
OTP_DAILY_LIMIT = 5
OTP_VERIFIED_WINDOW = 900   # seconds a verified email stays valid


def normalize_email(raw):
    """Return a lower-case email if it looks valid, else None."""
    email = (raw or "").strip().lower()

    if len(email) <= 254 and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return email

    return None


def _hash_otp(identifier, purpose, otp):
    key = app.secret_key.encode()
    message = f"{identifier}:{purpose}:{otp}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def deliver_email(email, otp):
    """Send the OTP by SMTP; if SMTP is not configured, print it (dev mode)."""
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")

    if not host or not user or not password:
        print()
        print("========================================")
        print(f"[DEV] OTP for {email}: {otp}")
        print("========================================")
        print()
        return True

    port = int(os.environ.get("SMTP_PORT", "587"))
    sender = os.environ.get("SMTP_FROM") or user

    message = EmailMessage()
    message["Subject"] = "Your QGEN-AI verification code"
    message["From"] = sender
    message["To"] = email
    message.set_content(
    f"QGEN-AI | Email Verification\n\n"
    f"Dear User,\n\n"
    f"We received a request to verify your email address for your QGEN-AI account.\n\n"
    f"Your verification code is:\n\n"
    f"    {otp}\n\n"
    f"Validity: {OTP_TTL_MIN} minutes\n\n"
    f"Please enter this code in the QGEN-AI registration page to complete your email verification.\n\n"
    f"Security Notice:\n"
    f"• Do not share this code with anyone.\n"
    f"• This code will expire after {OTP_TTL_MIN} minutes.\n"
    f"• If you did not initiate this request, you can safely ignore this email.\n\n"
    f"Thank you for choosing QGEN-AI.\n\n"
    f"Best regards,\n"
    f"QGEN-AI Team"
)

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(message)
        return True
    except Exception as e:
        print("Email send error:", e)
        return False


def send_reset_email(email, link):
    """Email a password-reset link; in dev mode (no SMTP) print it instead."""
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")

    if not host or not user or not password:
        print()
        print("========================================")
        print(f"[DEV] Password reset link for {email}:")
        print(link)
        print("========================================")
        print()
        return True

    port = int(os.environ.get("SMTP_PORT", "587"))
    sender = os.environ.get("SMTP_FROM") or user

    message = EmailMessage()
    message["Subject"] = "Reset your QGEN-AI password"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        "Use the link below to reset your QGEN-AI password. "
        "It is valid for 30 minutes.\n\n"
        f"{link}\n\n"
        "If you did not request this, you can ignore this email."
    )

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(message)
        return True
    except Exception as e:
        print("Reset email send error:", e)
        return False


def create_and_send_otp(identifier, purpose):
    """Returns (ok, message)."""
    connection = get_connection()

    if connection is None:
        return False, "Database connection failed."

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT created_at FROM otp_verifications "
            "WHERE identifier = %s AND purpose = %s ORDER BY id DESC LIMIT 1",
            (identifier, purpose)
        )
        last = cursor.fetchone()

        if last and (datetime.now() - last["created_at"]).total_seconds() < OTP_RESEND_SECONDS:
            return False, "Please wait a minute before requesting another OTP."

        cursor.execute(
            "SELECT COUNT(*) AS total FROM otp_verifications "
            "WHERE identifier = %s AND created_at >= NOW() - INTERVAL 1 DAY",
            (identifier,)
        )

        if cursor.fetchone()["total"] >= OTP_DAILY_LIMIT:
            return False, "Daily OTP limit reached. Try again tomorrow."

        otp = f"{secrets.randbelow(10 ** 6):06d}"

        cursor.execute(
            "DELETE FROM otp_verifications "
            "WHERE identifier = %s AND purpose = %s AND expires_at < NOW()",
            (identifier, purpose)
        )

        cursor.execute(
            "INSERT INTO otp_verifications (identifier, purpose, otp_hash, expires_at) "
            "VALUES (%s, %s, %s, %s)",
            (
                identifier,
                purpose,
                _hash_otp(identifier, purpose, otp),
                datetime.now() + timedelta(minutes=OTP_TTL_MIN)
            )
        )

        connection.commit()

    except Exception as e:
        connection.rollback()
        print("Create OTP error:", e)
        return False, "Unable to create OTP."

    finally:
        cursor.close()
        connection.close()

    if not deliver_email(identifier, otp):
        return False, "Could not send the email. Please try again."

    return True, "OTP sent to your email."


def check_otp(identifier, purpose, otp):
    """True only for a valid, unexpired, unused OTP."""
    connection = get_connection()

    if connection is None:
        return False

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT id, otp_hash, expires_at, attempts FROM otp_verifications "
            "WHERE identifier = %s AND purpose = %s ORDER BY id DESC LIMIT 1",
            (identifier, purpose)
        )
        record = cursor.fetchone()

        if (
            not record
            or datetime.now() > record["expires_at"]
            or record["attempts"] >= OTP_MAX_ATTEMPTS
        ):
            return False

        cursor.execute(
            "UPDATE otp_verifications SET attempts = attempts + 1 WHERE id = %s",
            (record["id"],)
        )
        connection.commit()

        if hmac.compare_digest(record["otp_hash"], _hash_otp(identifier, purpose, otp)):
            cursor.execute(
                "DELETE FROM otp_verifications WHERE id = %s",
                (record["id"],)
            )
            connection.commit()
            return True

        return False

    except Exception as e:
        connection.rollback()
        print("Check OTP error:", e)
        return False

    finally:
        cursor.close()
        connection.close()


def email_already_registered(email):
    connection = get_connection()

    if connection is None:
        return False

    cursor = connection.cursor()

    try:
        cursor.execute("SELECT 1 FROM faculty WHERE email = %s LIMIT 1", (email,))
        if cursor.fetchone():
            return True

        cursor.execute("SELECT 1 FROM student WHERE email = %s LIMIT 1", (email,))
        return cursor.fetchone() is not None

    except Exception as e:
        print("Email lookup error:", e)
        return False

    finally:
        cursor.close()
        connection.close()


def ensure_otp_schema():
    """Creates the OTP table, or converts the old phone-based one."""
    connection = get_connection()

    if connection is None:
        return

    cursor = connection.cursor()

    try:
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS otp_verifications ("
            "id INT AUTO_INCREMENT PRIMARY KEY, "
            "identifier VARCHAR(254) NOT NULL, "
            "purpose VARCHAR(20) NOT NULL, "
            "otp_hash CHAR(64) NOT NULL, "
            "expires_at DATETIME NOT NULL, "
            "attempts INT NOT NULL DEFAULT 0, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "INDEX idx_identifier_purpose (identifier, purpose)"
            ")"
        )

        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'otp_verifications' AND COLUMN_NAME = 'phone'"
        )

        if cursor.fetchone()[0] > 0:
            cursor.execute("DELETE FROM otp_verifications")
            cursor.execute(
                "ALTER TABLE otp_verifications "
                "CHANGE phone identifier VARCHAR(254) NOT NULL"
            )

        connection.commit()

    except Exception as e:
        connection.rollback()
        print("OTP schema error:", e)

    finally:
        cursor.close()
        connection.close()


def ensure_student_schema():
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


@app.route("/otp/send", methods=["POST"])
def otp_send():

    email = normalize_email(request.form.get("email"))

    if not email:
        return jsonify(ok=False, message="Enter a valid email address."), 400

    if email_already_registered(email):
        return jsonify(ok=False, message="This email is already registered."), 409

    ok, message = create_and_send_otp(email, "register")

    return jsonify(ok=ok, message=message), (200 if ok else 429)


@app.route("/otp/verify", methods=["POST"])
def otp_verify():

    email = normalize_email(request.form.get("email"))
    code = request.form.get("code", "").strip()

    if not email or not re.fullmatch(r"\d{6}", code):
        return jsonify(ok=False, message="Invalid code."), 400

    if check_otp(email, "register", code):
        session["verified_email"] = email
        session["verified_email_at"] = datetime.now().timestamp()
        return jsonify(ok=True, message="Email verified.")

    return jsonify(ok=False, message="Incorrect or expired OTP."), 401


# ============================================================
# HOD DASHBOARD
# ============================================================

@app.route("/hod/dashboard")
def hod_dashboard():

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("faculty_login"))

    cursor = connection.cursor(dictionary=True)

    faculty_count = 0
    subject_count = 0
    syllabus_count = 0
    paper_count = 0

    try:

        hod_department = session.get("department")

        if hod_department:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM faculty
                WHERE department = %s
                AND LOWER(role) = 'faculty'
                """,
                (hod_department,)
            )
        else:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM faculty
                WHERE LOWER(role) = 'faculty'
                """
            )

        result = cursor.fetchone()
        if result:
            faculty_count = result["total"] or 0

        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM subject
            WHERE LOWER(department) = LOWER(%s)
            """,
            (hod_department,)
        )
        result = cursor.fetchone()
        if result:
            subject_count = result["total"] or 0

        if hod_department:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM syllabi s
                INNER JOIN faculty f
                    ON s.faculty_id = f.faculty_id
                WHERE f.department = %s
                """,
                (hod_department,)
            )
        else:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM syllabi
                """
            )

        result = cursor.fetchone()
        if result:
            syllabus_count = result["total"] or 0

        if hod_department:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM question_papers qp
                INNER JOIN faculty f
                    ON qp.faculty_id = f.faculty_id
                WHERE f.department = %s
                """,
                (hod_department,)
            )
        else:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM question_papers
                """
            )

        result = cursor.fetchone()
        if result:
            paper_count = result["total"] or 0

    except Exception as e:
        print("HOD dashboard error:", e)
        flash("Unable to load dashboard statistics.", "error")

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "hod_dashboard.html",
        faculty_count=faculty_count,
        subject_count=subject_count,
        syllabus_count=syllabus_count,
        paper_count=paper_count
    )


# ============================================================
# HOD MANAGE FACULTY
# ============================================================

@app.route("/hod/faculty")
def hod_faculty():

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_dashboard"))

    cursor = connection.cursor(dictionary=True)

    faculty = []

    try:

        hod_department = session.get("department")

        if hod_department:
            cursor.execute(
                """
                SELECT
                    faculty_id,
                    name,
                    email,
                    department,
                    year_of_experience,
                    role,
                    status
                FROM faculty
                WHERE department = %s
                ORDER BY faculty_id ASC
                """,
                (hod_department,)
            )
        else:
            cursor.execute(
                """
                SELECT
                    faculty_id,
                    name,
                    email,
                    department,
                    year_of_experience,
                    role,
                    status
                FROM faculty
                ORDER BY faculty_id ASC
                """
            )

        faculty = cursor.fetchall()
        faculty.sort(key=lambda faculty: int(faculty["faculty_id"]))

    except Exception as e:
        print("HOD faculty error:", e)
        flash("Unable to load faculty.", "error")

    finally:
        cursor.close()
        connection.close()

    return render_template("hod_faculty.html", faculty=faculty)


# ============================================================
# HOD ADD FACULTY
# ============================================================

@app.route("/hod/faculty/add", methods=["GET", "POST"])
def hod_add_faculty():

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    hod_department = session.get("department", "").strip()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        year_of_experience_raw = request.form.get("year_of_experience", "0").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name:
            flash("Please enter the faculty name.", "error")
            return redirect(url_for("hod_add_faculty"))

        if not email:
            flash("Please enter the faculty email.", "error")
            return redirect(url_for("hod_add_faculty"))

        try:
            year_of_experience = int(year_of_experience_raw or 0)
        except ValueError:
            flash("Year of experience must be a whole number.", "error")
            return redirect(url_for("hod_add_faculty"))

        if year_of_experience < 0:
            flash("Year of experience cannot be negative.", "error")
            return redirect(url_for("hod_add_faculty"))

        if not password:
            flash("Please enter a password.", "error")
            return redirect(url_for("hod_add_faculty"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return redirect(url_for("hod_add_faculty"))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("hod_add_faculty"))

        if not hod_department:
            flash("HOD department is not available in the current session.", "error")
            return redirect(url_for("hod_faculty"))

        connection = get_connection()

        if connection is None:
            flash("Database connection failed.", "error")
            return redirect(url_for("hod_add_faculty"))

        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT faculty_id
                FROM faculty
                WHERE email = %s
                """,
                (email,)
            )

            if cursor.fetchone():
                flash("Email is already registered.", "error")
                return redirect(url_for("hod_add_faculty"))

            password_hash = generate_password_hash(password)

            cursor.execute(
                """
                INSERT INTO faculty
                (
                    name,
                    email,
                    department,
                    year_of_experience,
                    password,
                    role,
                    status
                )
                VALUES
                (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    name,
                    email,
                    hod_department,
                    year_of_experience,
                    password_hash,
                    "faculty",
                    "Active"
                )
            )

            connection.commit()

            flash(f"Faculty {name} added successfully to {hod_department}.", "success")

        except Exception as e:
            connection.rollback()
            print("HOD add faculty error:", e)
            flash("Unable to add faculty. Please check the faculty table fields.", "error")

        finally:
            cursor.close()
            connection.close()

        return redirect(url_for("hod_faculty"))

    return render_template("hod_add_faculty.html", hod_department=hod_department)


# ============================================================
# HOD MANAGE SUBJECTS
# ============================================================

@app.route("/hod/subjects")
def hod_subjects():

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_dashboard"))

    cursor = connection.cursor(dictionary=True)

    subjects = []

    try:
        cursor.execute(
            """
            SELECT
                course_id,
                subject_id,
                subject_name,
                credits,
                total_marks,
                see_marks,
                cia_marks,
                hours_per_subject,
                cia_pattern,
                cia_duration,
                see_pattern,
                see_duration
            FROM subject
            WHERE LOWER(department) = LOWER(%s)
            ORDER BY course_id, subject_id
            """,
            (session.get("department", ""),)
        )
        subjects = cursor.fetchall()

    except Exception as e:
        print("HOD subjects error:", e)
        flash("Unable to load subjects.", "error")

    finally:
        cursor.close()
        connection.close()

    return render_template("hod_subjects.html", subjects=subjects)


# ============================================================
# ADD SUBJECT
# ============================================================

@app.route("/hod/subjects/add", methods=["POST"])
def add_subject():

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    course_id = request.form.get("course_id", "").strip()
    subject_id = request.form.get("subject_id", "").strip()
    subject_name = request.form.get("subject_name", "").strip()
    credits = request.form.get("credits", "").strip()
    total_marks = request.form.get("total_marks", "").strip()
    see_marks = request.form.get("see_marks", "").strip()
    cia_marks = request.form.get("cia_marks", "").strip()
    hours_per_subject = request.form.get("hours_per_subject", "").strip()
    cia_pattern = request.form.get("cia_pattern", "").strip()
    cia_duration = request.form.get("cia_duration", "").strip()
    see_pattern = request.form.get("see_pattern", "").strip()
    see_duration = request.form.get("see_duration", "").strip()

    if not course_id or not subject_id or not subject_name:
        flash("Course ID, Subject ID and Subject Name are required.", "error")
        return redirect(url_for("hod_subjects"))

    if not credits or not total_marks:
        flash("Credits and Total Marks are required.", "error")
        return redirect(url_for("hod_subjects"))

    if not see_marks or not cia_marks:
        flash("CIA Marks and SEE Marks are required.", "error")
        return redirect(url_for("hod_subjects"))

    if not hours_per_subject:
        flash("Hours per subject are required.", "error")
        return redirect(url_for("hod_subjects"))

    try:
        credits = int(credits)
        total_marks = int(total_marks)
        see_marks = int(see_marks)
        cia_marks = int(cia_marks)
        hours_per_subject = int(hours_per_subject)
    except ValueError:
        flash("Marks, credits and hours must be valid numbers.", "error")
        return redirect(url_for("hod_subjects"))

    if see_marks + cia_marks != total_marks:
        flash("SEE Marks + CIA Marks must equal Total Marks.", "error")
        return redirect(url_for("hod_subjects"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_subjects"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT subject_id
            FROM subject
            WHERE subject_id = %s
            """,
            (subject_id,)
        )

        existing_subject = cursor.fetchone()

        if existing_subject:
            flash("Subject ID already exists.", "error")
            return redirect(url_for("hod_subjects"))

        cursor.execute(
            """
            INSERT INTO subject
            (
                course_id,
                subject_id,
                subject_name,
                credits,
                total_marks,
                see_marks,
                cia_marks,
                hours_per_subject,
                cia_pattern,
                cia_duration,
                see_pattern,
                see_duration,
                department
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s
            )
            """,
            (
                course_id,
                subject_id,
                subject_name,
                credits,
                total_marks,
                see_marks,
                cia_marks,
                hours_per_subject,
                cia_pattern,
                cia_duration,
                see_pattern,
                see_duration,
                session.get("department", "")
            )
        )

        connection.commit()

        flash("Subject added successfully.", "success")

    except Exception as e:
        connection.rollback()
        print("Add subject error:", e)
        flash("Unable to add subject.", "error")

    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("hod_subjects"))


# ============================================================
# EDIT SUBJECT
# ============================================================

@app.route("/hod/subjects/<string:subject_id>/edit", methods=["GET", "POST"])
def edit_subject(subject_id):

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_subjects"))

    cursor = connection.cursor(dictionary=True)

    if request.method == "POST":

        course_id = request.form.get("course_id", "").strip()
        subject_name = request.form.get("subject_name", "").strip()
        credits = request.form.get("credits", "").strip()
        total_marks = request.form.get("total_marks", "").strip()
        see_marks = request.form.get("see_marks", "").strip()
        cia_marks = request.form.get("cia_marks", "").strip()
        hours_per_subject = request.form.get("hours_per_subject", "").strip()
        cia_pattern = request.form.get("cia_pattern", "").strip()
        cia_duration = request.form.get("cia_duration", "").strip()
        see_pattern = request.form.get("see_pattern", "").strip()
        see_duration = request.form.get("see_duration", "").strip()

        try:
            credits = int(credits)
            total_marks = int(total_marks)
            see_marks = int(see_marks)
            cia_marks = int(cia_marks)
            hours_per_subject = int(hours_per_subject)
        except ValueError:
            cursor.close()
            connection.close()
            flash("Marks, credits and hours must be valid numbers.", "error")
            return redirect(url_for("edit_subject", subject_id=subject_id))

        if see_marks + cia_marks != total_marks:
            cursor.close()
            connection.close()
            flash("SEE Marks + CIA Marks must equal Total Marks.", "error")
            return redirect(url_for("edit_subject", subject_id=subject_id))

        try:
            cursor.execute(
                """
                UPDATE subject
                SET
                    course_id = %s,
                    subject_name = %s,
                    credits = %s,
                    total_marks = %s,
                    see_marks = %s,
                    cia_marks = %s,
                    hours_per_subject = %s,
                    cia_pattern = %s,
                    cia_duration = %s,
                    see_pattern = %s,
                    see_duration = %s
                WHERE subject_id = %s
                AND LOWER(department) = LOWER(%s)
                """,
                (
                    course_id,
                    subject_name,
                    credits,
                    total_marks,
                    see_marks,
                    cia_marks,
                    hours_per_subject,
                    cia_pattern,
                    cia_duration,
                    see_pattern,
                    see_duration,
                    subject_id,
                    session.get("department", "")
                )
            )

            connection.commit()

            flash("Subject updated successfully.", "success")

        except Exception as e:
            connection.rollback()
            print("Edit subject error:", e)
            flash("Unable to update subject.", "error")

        finally:
            cursor.close()
            connection.close()

        return redirect(url_for("hod_subjects"))

    try:
        cursor.execute(
            """
            SELECT
                course_id,
                subject_id,
                subject_name,
                credits,
                total_marks,
                see_marks,
                cia_marks,
                hours_per_subject,
                cia_pattern,
                cia_duration,
                see_pattern,
                see_duration
            FROM subject
            WHERE subject_id = %s
            AND LOWER(department) = LOWER(%s)
            """,
            (subject_id, session.get("department", ""))
        )

        subject = cursor.fetchone()

    except Exception as e:
        print("Load subject error:", e)
        subject = None

    finally:
        cursor.close()
        connection.close()

    if not subject:
        flash("Subject not found.", "error")
        return redirect(url_for("hod_subjects"))

    return render_template("hod_edit_subject.html", subject=subject)


# ============================================================
# DELETE SUBJECT
# ============================================================

@app.route("/hod/subjects/<string:subject_id>/delete", methods=["POST"])
def delete_subject(subject_id):

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_subjects"))

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT subject_id
            FROM subject
            WHERE subject_id = %s
            AND LOWER(department) = LOWER(%s)
            """,
            (subject_id, session.get("department", ""))
        )

        subject = cursor.fetchone()

        if not subject:
            flash("Subject not found.", "error")
            return redirect(url_for("hod_subjects"))

        cursor.execute(
            """
            DELETE FROM subject
            WHERE subject_id = %s
            AND LOWER(department) = LOWER(%s)
            """,
            (subject_id, session.get("department", ""))
        )

        connection.commit()

        flash("Subject deleted successfully.", "success")

    except Exception as e:
        connection.rollback()
        print("Delete subject error:", e)
        flash("Unable to delete subject. It may be used by another table.", "error")

    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("hod_subjects"))


# ============================================================
# HOD ASSIGN SUBJECT
# ============================================================

@app.route("/hod/assign-subject", methods=["GET", "POST"])
def hod_assign_subject():

    if not hod_required():
        flash("Please login as HOD.", "error")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_dashboard"))

    cursor = connection.cursor(dictionary=True)

    try:

        hod_department = session.get("department")

        if request.method == "POST":

            subject_id = request.form.get("subject_id", "").strip()
            faculty_id = request.form.get("faculty_id", "").strip()
            academic_year = request.form.get("academic_year", "").strip()
            section_id = request.form.get("section_id", "").strip()
            student_year = request.form.get("student_year", "").strip()

            if not subject_id or not faculty_id:
                flash("Subject and Faculty are required.", "error")
                return redirect(url_for("hod_assign_subject"))

            if not academic_year or not section_id:
                flash("Academic Year and Section are required.", "error")
                return redirect(url_for("hod_assign_subject"))

            if student_year not in ("1", "2", "3", "4"):
                flash("Please select the Student Year.", "error")
                return redirect(url_for("hod_assign_subject"))

            try:
                faculty_id = int(faculty_id)
            except ValueError:
                flash("Invalid Faculty ID.", "error")
                return redirect(url_for("hod_assign_subject"))

            if hod_department:
                cursor.execute(
                    """
                    SELECT faculty_id, name, department
                    FROM faculty
                    WHERE faculty_id = %s
                    AND department = %s
                    AND role = 'faculty'
                    """,
                    (faculty_id, hod_department)
                )
            else:
                cursor.execute(
                    """
                    SELECT faculty_id, name, department
                    FROM faculty
                    WHERE faculty_id = %s
                    AND role = 'faculty'
                    """,
                    (faculty_id,)
                )

            faculty_member = cursor.fetchone()

            if not faculty_member:
                flash("Faculty member not found in your department.", "error")
                return redirect(url_for("hod_assign_subject"))

            cursor.execute(
                """
                SELECT subject_id, subject_name, course_id, semester
                FROM subject
                WHERE subject_id = %s
                AND LOWER(department) = LOWER(%s)
                """,
                (subject_id, hod_department or "")
            )

            subject = cursor.fetchone()

            if not subject:
                flash("Subject not found.", "error")
                return redirect(url_for("hod_assign_subject"))

            # Student year must match the subject's semester
            subject_semester = subject.get("semester")

            if subject_semester:
                expected_year = (int(subject_semester) + 1) // 2

                if int(student_year) != expected_year:
                    flash(
                        f"{subject['subject_name']} is a Semester "
                        f"{subject_semester} subject, so it belongs to "
                        f"Year {expected_year}, not Year {student_year}.",
                        "error"
                    )
                    return redirect(url_for("hod_assign_subject"))

            cursor.execute(
                """
                SELECT subject_id, faculty_id, academic_year, section_id
                FROM subject_faculty
                WHERE subject_id = %s
                AND faculty_id = %s
                AND academic_year = %s
                AND section_id = %s
                AND student_year = %s
                """,
                (subject_id, faculty_id, academic_year, section_id,
                 int(student_year))
            )

            existing = cursor.fetchone()

            if existing:
                flash(
                    "This subject is already assigned to this faculty "
                    "for the selected academic year, student year and section.",
                    "error"
                )
                return redirect(url_for("hod_assign_subject"))

            cursor.execute(
                """
                INSERT INTO subject_faculty
                (
                    subject_id,
                    faculty_id,
                    academic_year,
                    section_id,
                    student_year
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (subject_id, faculty_id, academic_year, section_id,
                 int(student_year))
            )

            connection.commit()

            flash("Subject assigned successfully.", "success")

            return redirect(url_for("hod_assign_subject"))

        cursor.execute(
            """
            SELECT subject_id, subject_name, course_id, semester
            FROM subject
            WHERE LOWER(department) = LOWER(%s)
            ORDER BY course_id, semester, subject_id
            """,
            (hod_department or "",)
        )

        subjects = cursor.fetchall()

        if hod_department:
            cursor.execute(
                """
                SELECT faculty_id, name, email, department
                FROM faculty
                WHERE department = %s
                AND role = 'faculty'
                ORDER BY faculty_id ASC
                """,
                (hod_department,)
            )
        else:
            cursor.execute(
                """
                SELECT faculty_id, name, email, department
                FROM faculty
                WHERE role = 'faculty'
                ORDER BY faculty_id ASC
                """
            )

        faculty = cursor.fetchall()

        if hod_department:
            cursor.execute(
                """
                SELECT
                    sf.subject_id,
                    s.subject_name,
                    sf.faculty_id,
                    sf.academic_year,
                    sf.section_id,
                    sf.student_year,
                    f.name AS faculty_name,
                    s.course_id,
                    s.semester
                FROM subject_faculty sf
                LEFT JOIN faculty f ON sf.faculty_id = f.faculty_id
                LEFT JOIN subject s ON sf.subject_id = s.subject_id
                WHERE f.department = %s
                ORDER BY
                    sf.academic_year DESC,
                    sf.section_id,
                    sf.subject_id
                """,
                (hod_department,)
            )
        else:
            cursor.execute(
                """
                SELECT
                    sf.subject_id,
                    s.subject_name,
                    sf.faculty_id,
                    sf.academic_year,
                    sf.section_id,
                    sf.student_year,
                    f.name AS faculty_name,
                    s.course_id,
                    s.semester
                FROM subject_faculty sf
                LEFT JOIN faculty f ON sf.faculty_id = f.faculty_id
                LEFT JOIN subject s ON sf.subject_id = s.subject_id
                ORDER BY
                    sf.academic_year DESC,
                    sf.section_id,
                    sf.subject_id
                """
            )

        assignments = cursor.fetchall()

    except Exception as e:
        connection.rollback()
        print("HOD assign subject error:", e)
        flash("Unable to process subject assignment.", "error")
        subjects = []
        faculty = []
        assignments = []

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "hod_assign_subject.html",
        subjects=subjects,
        faculty=faculty,
        assignments=assignments
    )
# ============================================================
# DELETE SUBJECT ASSIGNMENT
# ============================================================

@app.route(
    "/hod/assign-subject/delete/<string:subject_id>/<int:faculty_id>/<string:academic_year>/<string:section_id>",
    methods=["POST"]
)
def delete_subject_assignment(subject_id, faculty_id, academic_year, section_id):

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    # student_year comes from the hidden field in the delete form.
    # An empty value means an older assignment saved before student_year existed (NULL).
    student_year_raw = request.form.get("student_year", "").strip()

    if student_year_raw:
        if student_year_raw not in ("1", "2", "3", "4"):
            flash("Invalid student year.", "error")
            return redirect(url_for("hod_assign_subject"))
        student_year = int(student_year_raw)
        year_condition = "AND sf.student_year = %s"
        year_params = (student_year,)
    else:
        year_condition = "AND sf.student_year IS NULL"
        year_params = ()

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_assign_subject"))

    cursor = connection.cursor(dictionary=True)

    try:

        hod_department = session.get("department")

        if hod_department:
            cursor.execute(
                f"""
                SELECT
                    sf.subject_id,
                    sf.faculty_id,
                    sf.academic_year,
                    sf.section_id,
                    sf.student_year
                FROM subject_faculty sf

                INNER JOIN faculty f
                    ON sf.faculty_id = f.faculty_id

                WHERE sf.subject_id = %s
                AND sf.faculty_id = %s
                AND sf.academic_year = %s
                AND sf.section_id = %s
                {year_condition}
                AND f.department = %s
                """,
                (subject_id, faculty_id, academic_year, section_id)
                + year_params
                + (hod_department,)
            )
        else:
            cursor.execute(
                f"""
                SELECT
                    sf.subject_id,
                    sf.faculty_id,
                    sf.academic_year,
                    sf.section_id,
                    sf.student_year
                FROM subject_faculty sf

                WHERE sf.subject_id = %s
                AND sf.faculty_id = %s
                AND sf.academic_year = %s
                AND sf.section_id = %s
                {year_condition}
                """,
                (subject_id, faculty_id, academic_year, section_id)
                + year_params
            )

        assignment = cursor.fetchone()

        if not assignment:
            flash("Subject assignment not found.", "error")
            return redirect(url_for("hod_assign_subject"))

        cursor.execute(
            f"""
            DELETE sf FROM subject_faculty sf

            WHERE sf.subject_id = %s
            AND sf.faculty_id = %s
            AND sf.academic_year = %s
            AND sf.section_id = %s
            {year_condition}
            """,
            (subject_id, faculty_id, academic_year, section_id)
            + year_params
        )

        connection.commit()

        flash("Subject assignment removed successfully.", "success")

    except Exception as e:
        connection.rollback()
        print("Delete subject assignment error:", e)
        flash("Unable to remove subject assignment.", "error")

    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("hod_assign_subject"))

# ============================================================
# HOD SYLLABUS
# ============================================================

@app.route("/hod/syllabus")
def hod_syllabus():

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_dashboard"))

    cursor = connection.cursor(dictionary=True)

    syllabi = []

    try:

        hod_department = session.get("department")

        if hod_department:
            cursor.execute(
                """
                SELECT
                    s.id,
                    s.subject,
                    s.filename,
                    s.filepath,
                    s.uploaded_at,
                    s.download_count,
                    f.name AS faculty_name
                FROM syllabi s
                LEFT JOIN faculty f
                    ON s.faculty_id = f.faculty_id
                WHERE f.department = %s
                ORDER BY s.uploaded_at DESC
                """,
                (hod_department,)
            )
        else:
            cursor.execute(
                """
                SELECT
                    s.id,
                    s.subject,
                    s.filename,
                    s.filepath,
                    s.uploaded_at,
                    s.download_count,
                    f.name AS faculty_name
                FROM syllabi s
                LEFT JOIN faculty f
                    ON s.faculty_id = f.faculty_id
                ORDER BY s.uploaded_at DESC
                """
            )

        syllabi = cursor.fetchall()

    except Exception as e:
        print("HOD syllabus error:", e)
        flash("Unable to load syllabi.", "error")

    finally:
        cursor.close()
        connection.close()

    return render_template("hod_syllabus.html", syllabi=syllabi)


# ============================================================
# VIEW SYLLABUS
# ============================================================

@app.route("/hod/syllabus/view/<int:syllabus_id>")
def view_syllabus(syllabus_id):

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_syllabus"))

    cursor = connection.cursor(dictionary=True)

    syllabus = None

    try:
        cursor.execute(
            """
            SELECT
                filepath,
                filename
            FROM syllabi s
            INNER JOIN faculty f
                ON s.faculty_id = f.faculty_id
            WHERE s.id = %s
            AND f.department = %s
            """,
            (syllabus_id, session.get("department", ""))
        )

        syllabus = cursor.fetchone()

    except Exception as e:
        print("View syllabus error:", e)

    finally:
        cursor.close()
        connection.close()

    if not syllabus:
        flash("Syllabus not found.", "error")
        return redirect(url_for("hod_syllabus"))

    filepath = syllabus["filepath"]

    if not filepath or not os.path.exists(filepath):
        flash("Syllabus file not found.", "error")
        return redirect(url_for("hod_syllabus"))

    directory = os.path.dirname(filepath)
    filename = os.path.basename(filepath)

    return send_from_directory(
        directory,
        filename,
        as_attachment=False,
        download_name=syllabus["filename"]
    )


# ============================================================
# DOWNLOAD SYLLABUS
# ============================================================

@app.route("/hod/syllabus/download/<int:syllabus_id>")
def hod_download_syllabus(syllabus_id):

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_syllabus"))

    cursor = connection.cursor(dictionary=True)

    syllabus = None

    try:
        cursor.execute(
            """
            SELECT
                filepath,
                filename
            FROM syllabi s
            INNER JOIN faculty f
                ON s.faculty_id = f.faculty_id
            WHERE s.id = %s
            AND f.department = %s
            """,
            (syllabus_id, session.get("department", ""))
        )

        syllabus = cursor.fetchone()

        if syllabus:
            cursor.execute(
                """
                UPDATE syllabi
                SET download_count =
                    download_count + 1
                WHERE id = %s
                """,
                (syllabus_id,)
            )

            connection.commit()

    except Exception as e:
        connection.rollback()
        print("Download syllabus error:", e)

    finally:
        cursor.close()
        connection.close()

    if not syllabus:
        flash("Syllabus not found.", "error")
        return redirect(url_for("hod_syllabus"))

    filepath = syllabus["filepath"]

    if not filepath or not os.path.exists(filepath):
        flash("Syllabus file not found.", "error")
        return redirect(url_for("hod_syllabus"))

    directory = os.path.dirname(filepath)
    filename = os.path.basename(filepath)

    return send_from_directory(
        directory,
        filename,
        as_attachment=True,
        download_name=syllabus["filename"]
    )


# ============================================================
# HOD QUESTION PAPERS
# ============================================================

@app.route("/hod/question-papers")
def hod_question_papers():

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_dashboard"))

    cursor = connection.cursor(dictionary=True)

    question_papers = []

    try:

        hod_department = session.get("department")

        if hod_department:
            cursor.execute(
                """
                SELECT
                    qp.id,
                    qp.faculty_id,
                    qp.subject,
                    qp.course,
                    qp.total_marks,
                    qp.difficulty,
                    qp.num_questions,
                    qp.pattern,
                    qp.created_at,
                    f.name AS faculty_name
                FROM question_papers qp
                LEFT JOIN faculty f
                    ON qp.faculty_id = f.faculty_id
                WHERE f.department = %s
                ORDER BY qp.created_at DESC
                """,
                (hod_department,)
            )
        else:
            cursor.execute(
                """
                SELECT
                    qp.id,
                    qp.faculty_id,
                    qp.subject,
                    qp.course,
                    qp.total_marks,
                    qp.difficulty,
                    qp.num_questions,
                    qp.pattern,
                    qp.created_at,
                    f.name AS faculty_name
                FROM question_papers qp
                LEFT JOIN faculty f
                    ON qp.faculty_id = f.faculty_id
                ORDER BY qp.created_at DESC
                """
            )

        question_papers = cursor.fetchall()

    except Exception as e:
        print("HOD question papers error:", e)
        flash("Unable to load question papers.", "error")

    finally:
        cursor.close()
        connection.close()

    return render_template("hod_question_papers.html", question_papers=question_papers)


# ============================================================
# HOD VIEW QUESTION PAPER
# ============================================================

@app.route("/hod/question-papers/<int:paper_id>")
def hod_view_question_paper(paper_id):

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_question_papers"))

    cursor = connection.cursor(dictionary=True)

    paper = None

    try:
        cursor.execute(
            """
            SELECT
                qp.id,
                qp.faculty_id,
                qp.subject,
                qp.course,
                qp.total_marks,
                qp.difficulty,
                qp.num_questions,
                qp.pattern,
                qp.exam_date,
                qp.syllabus,
                qp.questions,
                qp.created_at,
                f.name AS faculty_name
            FROM question_papers qp
            INNER JOIN faculty f
                ON qp.faculty_id = f.faculty_id
            WHERE qp.id = %s
            AND f.department = %s
            """,
            (paper_id, session.get("department", ""))
        )

        paper = cursor.fetchone()

    except Exception as e:
        print("HOD view question paper error:", e)

    finally:
        cursor.close()
        connection.close()

    if not paper:
        flash("Question paper not found.", "error")
        return redirect(url_for("hod_question_papers"))

    try:
        questions = json.loads(paper.get("questions") or "[]")
    except (TypeError, ValueError):
        questions = []

    hod_total_marks = int(paper["total_marks"])
    paper["paper_code"] = f"QP-{paper['id']:04d}"

    hod_mixed_counts = {}
    hod_mixed_given_counts = {}

    if paper["pattern"] == "Mixed":
        hod_mixed_counts = get_mixed_section_counts(hod_total_marks) or {}
        hod_mixed_given_counts = get_mixed_given_counts(hod_total_marks) or {}

    hod_attempt_count = (get_pattern_counts(paper["pattern"], hod_total_marks) or {}).get("attempt")

    return render_template(
        "hod_view_question_paper.html",
        paper=paper,
        questions=questions,
        college_name="B.M.S College for Women",
        exam_title="END – SEMESTER EXAMINATION",
        academic_session=session.get("academic_session", ""),
        duration=get_exam_duration(hod_total_marks),
        mixed_counts=hod_mixed_counts,
        mixed_given_counts=hod_mixed_given_counts,
        attempt_count=hod_attempt_count
    )


# ============================================================
# DOWNLOAD QUESTION PAPER
# ============================================================

@app.route("/hod/question-papers/download/<int:paper_id>")
def download_question_paper(paper_id):

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_question_papers"))

    cursor = connection.cursor(dictionary=True)

    paper = None

    try:
        cursor.execute(
            """
            SELECT
                qp.id,
                qp.questions
            FROM question_papers qp
            INNER JOIN faculty f
                ON qp.faculty_id = f.faculty_id
            WHERE qp.id = %s
            AND f.department = %s
            """,
            (paper_id, session.get("department", ""))
        )

        paper = cursor.fetchone()

    except Exception as e:
        print("Download question paper error:", e)

    finally:
        cursor.close()
        connection.close()

    if not paper:
        flash("Question paper not found.", "error")
        return redirect(url_for("hod_question_papers"))

    questions = paper.get("questions") or ""

    from io import BytesIO

    file_data = BytesIO(questions.encode("utf-8"))

    file_data.seek(0)

    return send_file(
        file_data,
        as_attachment=True,
        download_name=f"question_paper_{paper_id}.txt",
        mimetype="text/plain"
    )


# ============================================================
# HOD ANALYTICS
# ============================================================

@app.route("/hod/analytics")
def hod_analytics():

    if not hod_required():
        flash("Please login as HOD.")
        return redirect(url_for("faculty_login"))

    stats = {
        "papers_generated": 0,
        "syllabi_uploaded": 0,
        "total_questions": 0,
        "last_active": "N/A"
    }

    chart_data = {
        "months": [],
        "papers": [],
        "difficulty_labels": [],
        "difficulty_values": [],
        "pattern_labels": [],
        "pattern_values": [],
        "subject_labels": [],
        "subject_values": []
    }

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return render_template("hod_analytics.html", stats=stats, chart_data=chart_data)

    cursor = connection.cursor(dictionary=True)

    try:
        department = session.get("department")

        qp_join = "JOIN faculty f ON qp.faculty_id = f.faculty_id" if department else ""
        qp_where = "WHERE f.department = %s" if department else ""
        qp_params = (department,) if department else ()

        sy_join = "JOIN faculty f ON s.faculty_id = f.faculty_id" if department else ""
        sy_where = "WHERE f.department = %s" if department else ""
        sy_params = (department,) if department else ()

        cursor.execute(
            f"SELECT COUNT(*) AS total FROM question_papers qp {qp_join} {qp_where}",
            qp_params
        )
        row = cursor.fetchone()
        stats["papers_generated"] = row["total"] if row else 0

        cursor.execute(
            f"SELECT COUNT(*) AS total FROM syllabi s {sy_join} {sy_where}",
            sy_params
        )
        row = cursor.fetchone()
        stats["syllabi_uploaded"] = row["total"] if row else 0

        cursor.execute(
            f"SELECT COALESCE(SUM(qp.num_questions), 0) AS total FROM question_papers qp {qp_join} {qp_where}",
            qp_params
        )
        row = cursor.fetchone()
        stats["total_questions"] = row["total"] if row else 0

        cursor.execute(
            f"""
            SELECT MAX(latest) AS last_active
            FROM (
                SELECT MAX(qp.created_at) AS latest
                FROM question_papers qp
                {qp_join}
                {qp_where}

                UNION ALL

                SELECT MAX(s.uploaded_at) AS latest
                FROM syllabi s
                {sy_join}
                {sy_where}
            ) AS activity
            """,
            qp_params + sy_params
        )
        row = cursor.fetchone()
        if row and row.get("last_active"):
            value = row["last_active"]
            stats["last_active"] = value.strftime("%d %b %Y") if hasattr(value, "strftime") else str(value)

        cursor.execute(
            f"""
            SELECT
                DATE_FORMAT(qp.created_at, '%%Y-%%m') AS month_key,
                DATE_FORMAT(qp.created_at, '%%b %%Y') AS month_label,
                COUNT(*) AS total
            FROM question_papers qp
            {qp_join}
            {qp_where}
            {'AND' if qp_where else 'WHERE'} qp.created_at >= DATE_SUB(NOW(), INTERVAL 12 MONTH)
            GROUP BY month_key, month_label
            ORDER BY month_key ASC
            """,
            qp_params
        )
        rows = cursor.fetchall()
        chart_data["months"] = [r["month_label"] for r in rows]
        chart_data["papers"] = [r["total"] for r in rows]

        cursor.execute(
            f"""
            SELECT qp.difficulty, COUNT(*) AS total
            FROM question_papers qp
            {qp_join}
            {qp_where}
            GROUP BY qp.difficulty
            ORDER BY total DESC
            """,
            qp_params
        )
        rows = cursor.fetchall()
        chart_data["difficulty_labels"] = [r["difficulty"] for r in rows if r.get("difficulty")]
        chart_data["difficulty_values"] = [r["total"] for r in rows if r.get("difficulty")]

        cursor.execute(
            f"""
            SELECT qp.pattern, COUNT(*) AS total
            FROM question_papers qp
            {qp_join}
            {qp_where}
            GROUP BY qp.pattern
            ORDER BY total DESC
            """,
            qp_params
        )
        rows = cursor.fetchall()
        chart_data["pattern_labels"] = [r["pattern"] for r in rows if r.get("pattern")]
        chart_data["pattern_values"] = [r["total"] for r in rows if r.get("pattern")]

        cursor.execute(
            f"""
            SELECT qp.subject, COUNT(*) AS total
            FROM question_papers qp
            {qp_join}
            {qp_where}
            {'AND' if qp_where else 'WHERE'} qp.subject IS NOT NULL
              AND qp.subject <> ''
            GROUP BY qp.subject
            ORDER BY total DESC
            LIMIT 10
            """,
            qp_params
        )
        rows = cursor.fetchall()
        chart_data["subject_labels"] = [r["subject"] for r in rows]
        chart_data["subject_values"] = [r["total"] for r in rows]

    except Exception as e:
        print("HOD analytics error:", e)
    finally:
        cursor.close()
        connection.close()

    return render_template("hod_analytics.html", stats=stats, chart_data=chart_data)


# =========================================================
# HOME
# =========================================================

@app.route("/")
def index():
    return render_template("index.html")


# =========================================================
# ADMIN LOGIN
# =========================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter email and password.")
            return redirect(url_for("admin_login"))

        connection = get_connection()

        if connection is None:
            flash("Database connection failed.")
            return redirect(url_for("admin_login"))

        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT *
                FROM admin
                WHERE email = %s
                """,
                (email,)
            )
            admin = cursor.fetchone()
        finally:
            cursor.close()
            connection.close()

        if admin and check_password_hash(admin["password"], password):

            session.clear()
            session["role"] = "admin"
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            session["admin_email"] = admin["email"]

            return redirect(url_for("admin_dashboard"))

        flash("Invalid admin email or password.")

    return render_template("admin_login.html")


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin/dashboard")
def admin_dashboard():

    if not admin_required():
        flash("Please login as administrator.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("admin_login"))

    cursor = connection.cursor()

    try:
        cursor.execute("SELECT COUNT(*) FROM faculty")
        faculty_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM question_papers")
        paper_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM syllabi")
        syllabus_count = cursor.fetchone()[0]
    finally:
        cursor.close()
        connection.close()

    admin = None
    admin_id = session.get("admin_id")

    if admin_id:
        admin_connection = get_connection()
        if admin_connection is not None:
            admin_cursor = admin_connection.cursor(dictionary=True)
            try:
                admin_cursor.execute(
                    """
                    SELECT id, username, profile_image
                    FROM admin
                    WHERE id = %s
                    """,
                    (admin_id,)
                )
                admin = admin_cursor.fetchone()
            finally:
                admin_cursor.close()
                admin_connection.close()

    return render_template(
        "admin_dashboard.html",
        faculty_count=faculty_count,
        paper_count=paper_count,
        syllabus_count=syllabus_count,
        admin=admin
    )


# =========================================================
# ADMIN PROFILE
# =========================================================

@app.route("/admin/profile", methods=["GET", "POST"])
def admin_profile():

    if not admin_required():
        flash("Please login as administrator.")
        return redirect(url_for("admin_login"))

    admin_id = session.get("admin_id")

    if not admin_id:
        session.clear()
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("admin_dashboard"))

    cursor = connection.cursor(dictionary=True)

    if request.method == "POST":

        form_type = request.form.get("form_type", "")

        if form_type == "details":

            username = request.form.get("username", "").strip()

            if not username:
                flash("Please enter a username.")
                cursor.close()
                connection.close()
                return redirect(url_for("admin_profile"))

            try:
                cursor.execute(
                    """
                    UPDATE admin
                    SET username = %s
                    WHERE id = %s
                    """,
                    (username, admin_id)
                )
                connection.commit()
                session["admin_username"] = username
                flash("Profile updated successfully.", "success")
            except Exception as e:
                connection.rollback()
                print("Admin profile update error:", e)
                flash("Unable to update profile. The username may already be in use.")
            finally:
                cursor.close()
                connection.close()

            return redirect(url_for("admin_profile"))

        elif form_type == "password":

            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            try:
                cursor.execute(
                    """
                    SELECT password
                    FROM admin
                    WHERE id = %s
                    """,
                    (admin_id,)
                )
                admin_row = cursor.fetchone()

                if not admin_row or not check_password_hash(admin_row["password"], current_password):
                    flash("Current password is incorrect.")
                    return redirect(url_for("admin_profile"))

                if not new_password:
                    flash("Please enter a new password.")
                    return redirect(url_for("admin_profile"))

                if new_password != confirm_password:
                    flash("New passwords do not match.")
                    return redirect(url_for("admin_profile"))

                if len(new_password) < 8:
                    flash("Password must contain at least 8 characters.")
                    return redirect(url_for("admin_profile"))

                new_password_hash = generate_password_hash(new_password)

                cursor.execute(
                    """
                    UPDATE admin
                    SET password = %s
                    WHERE id = %s
                    """,
                    (new_password_hash, admin_id)
                )
                connection.commit()
                flash("Password changed successfully.", "success")

            except Exception as e:
                connection.rollback()
                print("Admin password change error:", e)
                flash("Unable to change password.")
            finally:
                cursor.close()
                connection.close()

            return redirect(url_for("admin_profile"))

        elif form_type == "photo":

            file = request.files.get("profile_image")

            if not file or file.filename == "":
                flash("Please choose an image to upload.")
                cursor.close()
                connection.close()
                return redirect(url_for("admin_profile"))

            if not allowed_image_file(file.filename):
                flash("Only PNG or JPG images are allowed.")
                cursor.close()
                connection.close()
                return redirect(url_for("admin_profile"))

            original_filename = secure_filename(file.filename)

            unique_name = (
                str(admin_id) + "_" + str(int(datetime.now().timestamp())) + "_" + original_filename
            )

            filepath = os.path.join(app.config["PROFILE_UPLOAD_FOLDER"], unique_name)

            file.save(filepath)

            try:
                cursor.execute(
                    """
                    UPDATE admin
                    SET profile_image = %s
                    WHERE id = %s
                    """,
                    (filepath, admin_id)
                )
                connection.commit()
                flash("Profile picture updated successfully.", "success")
            except Exception as e:
                connection.rollback()
                print("Admin profile picture update error:", e)
                flash("Unable to update profile picture.")
            finally:
                cursor.close()
                connection.close()

            return redirect(url_for("admin_profile"))

    try:
        cursor.execute(
            """
            SELECT
                id,
                username,
                email,
                profile_image,
                created_at
            FROM admin
            WHERE id = %s
            """,
            (admin_id,)
        )
        admin = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()

    if not admin:
        session.clear()
        flash("Administrator account not found.")
        return redirect(url_for("admin_login"))

    return render_template("admin_profile.html", admin=admin)


# =========================================================
# ADMIN PROFILE PICTURE (SERVE IMAGE)
# =========================================================

@app.route("/admin/profile-image/<int:admin_id>")
def admin_profile_image(admin_id):

    connection = get_connection()

    if connection is None:
        return "", 404

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT profile_image
            FROM admin
            WHERE id = %s
            """,
            (admin_id,)
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()

    if not row or not row["profile_image"]:
        return "", 404

    directory = os.path.dirname(row["profile_image"])
    filename = os.path.basename(row["profile_image"])

    return send_from_directory(directory, filename)


# =========================================================
# MANAGE FACULTIES
# =========================================================

@app.route("/admin/faculties")
def manage_faculties():

    if not admin_required():
        flash("Admin login required.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("admin_dashboard"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                faculty_id,
                name,
                email,
                department,
                role,
                status
            FROM faculty
            ORDER BY faculty_id asc
            """
        )
        faculties = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    return render_template("manage_faculties.html", faculties=faculties)


# =========================================================
# MAKE FACULTY HOD
# =========================================================

@app.route("/admin/faculties/<int:faculty_id>/make-hod", methods=["POST"])
def make_faculty_hod(faculty_id):

    if not admin_required():
        flash("Admin login required.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("manage_faculties"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                faculty_id,
                name,
                role
            FROM faculty
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )

        faculty = cursor.fetchone()

        if not faculty:
            flash("Faculty not found.")
            return redirect(url_for("manage_faculties"))

        if str(faculty["role"]).lower() == "hod":
            flash(f"{faculty['name']} is already an HOD.")
            return redirect(url_for("manage_faculties"))

        cursor.execute(
            """
            UPDATE faculty
            SET role = 'hod'
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )

        connection.commit()

        flash(f"{faculty['name']} has been assigned as HOD.", "success")

    except Exception as e:
        connection.rollback()
        print("Make HOD error:", e)
        flash("Unable to assign HOD role.")

    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("manage_faculties"))


# =========================================================
# REMOVE HOD ROLE
# =========================================================

@app.route("/admin/faculties/<int:faculty_id>/remove-hod", methods=["POST"])
def remove_hod_role(faculty_id):

    if not admin_required():
        flash("Admin login required.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("manage_faculties"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                faculty_id,
                name,
                role
            FROM faculty
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )

        faculty = cursor.fetchone()

        if not faculty:
            flash("Faculty not found.")
            return redirect(url_for("manage_faculties"))

        if str(faculty["role"]).lower() != "hod":
            flash(f"{faculty['name']} is not currently an HOD.")
            return redirect(url_for("manage_faculties"))

        cursor.execute(
            """
            UPDATE faculty
            SET role = 'faculty'
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )

        connection.commit()

        flash(f"HOD role removed from {faculty['name']}.", "success")

    except Exception as e:
        connection.rollback()
        print("Remove HOD error:", e)
        flash("Unable to remove HOD role.")

    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("manage_faculties"))


# =========================================================
# TOGGLE FACULTY STATUS (ACTIVE / DEACTIVATED)
# =========================================================

@app.route("/admin/faculties/<int:faculty_id>/toggle-status", methods=["POST"])
def toggle_faculty_status(faculty_id):

    if not admin_required():
        flash("Admin login required.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("manage_faculties"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                faculty_id,
                status
            FROM faculty
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )
        faculty = cursor.fetchone()

        if not faculty:
            flash("Faculty not found.")
            return redirect(url_for("manage_faculties"))

        current_status = faculty["status"] or "Active"
        new_status = "Deactivated" if current_status.lower() == "active" else "Active"

        cursor.execute(
            """
            UPDATE faculty
            SET status = %s
            WHERE faculty_id = %s
            """,
            (new_status, faculty_id)
        )
        connection.commit()
        flash(f"Faculty status updated to {new_status}.", "success")

    except Exception as e:
        connection.rollback()
        print("Toggle faculty status error:", e)
        flash("Unable to update faculty status.")
    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("manage_faculties"))


# =========================================================
# MANAGE QUESTION PAPERS (ADMIN)
# =========================================================

@app.route("/admin/question-papers")
def manage_question_papers():

    if not admin_required():
        flash("Admin login required.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("admin_dashboard"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                question_papers.id,
                question_papers.subject,
                question_papers.course,
                question_papers.total_marks,
                question_papers.difficulty,
                question_papers.num_questions,
                question_papers.pattern,
                question_papers.created_at,
                faculty.name AS faculty_name,
                faculty.email AS faculty_email
            FROM question_papers
            JOIN faculty
                ON faculty.faculty_id = question_papers.faculty_id
            ORDER BY question_papers.created_at DESC
            """
        )
        papers = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    return render_template("manage_question_papers.html", papers=papers)


# =========================================================
# VIEW QUESTION PAPER (ADMIN)
# =========================================================

@app.route("/admin/question-papers/<int:paper_id>")
def view_question_paper(paper_id):

    if not admin_required():
        flash("Admin login required.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("manage_question_papers"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                question_papers.*,
                faculty.name AS faculty_name
            FROM question_papers
            JOIN faculty
                ON faculty.faculty_id = question_papers.faculty_id
            WHERE question_papers.id = %s
            """,
            (paper_id,)
        )
        paper = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()

    if not paper:
        flash("Question paper not found.")
        return redirect(url_for("manage_question_papers"))

    try:
        questions = json.loads(paper["questions"])
    except (TypeError, ValueError):
        questions = []

    total_marks_int = int(paper["total_marks"])
    pattern = paper["pattern"]

    mixed_counts = {}
    mixed_given_counts = {}

    if pattern == "Mixed":
        mixed_counts = get_mixed_section_counts(total_marks_int) or {}
        mixed_given_counts = get_mixed_given_counts(total_marks_int) or {}

    attempt_count = (get_pattern_counts(pattern, total_marks_int) or {}).get("attempt")

    return render_template(
        "admin_view_question_paper.html",
        college_name="B.M.S College for Women",
        subject=paper["subject"],
        course=paper["course"],
        total_marks=total_marks_int,
        duration=get_exam_duration(total_marks_int),
        questions=questions,
        faculty_name=paper["faculty_name"],
        created_at=paper["created_at"],
        attempt_count=attempt_count,
        mixed_counts=mixed_counts,
        mixed_given_counts=mixed_given_counts
    )


# =========================================================
# DELETE QUESTION PAPER (ADMIN)
# ========================
# =================================

@app.route("/admin/question-papers/<int:paper_id>/delete", methods=["POST"])
def delete_question_paper(paper_id):

    if not admin_required():
        flash("Admin login required.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("manage_question_papers"))

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            DELETE FROM question_papers
            WHERE id = %s
            """,
            (paper_id,)
        )
        connection.commit()
        flash("Question paper deleted.", "success")
    except Exception as e:
        connection.rollback()
        print("Delete question paper error:", e)
        flash("Unable to delete question paper.")
    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("manage_question_papers"))


# =========================================================
# MANAGE SYLLABUS (ADMIN)
# =========================================================

@app.route("/admin/syllabus")
def manage_syllabi():

    if not admin_required():
        flash("Admin login required.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("admin_dashboard"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                syllabi.id,
                syllabi.subject,
                syllabi.filename,
                syllabi.uploaded_at,
                faculty.name AS faculty_name,
                faculty.email AS faculty_email
            FROM syllabi
            JOIN faculty
                ON faculty.faculty_id = syllabi.faculty_id
            ORDER BY syllabi.uploaded_at DESC
            """
        )
        syllabi = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    return render_template("manage_syllabi.html", syllabi=syllabi)


# =========================================================
# VIEW SYLLABUS PDF (ADMIN)
# =========================================================

@app.route("/admin/syllabus/view/<int:syllabus_id>")
def admin_view_syllabus_pdf(syllabus_id):

    if not admin_required():
        flash("Admin login required.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("manage_syllabi"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                filepath,
                filename
            FROM syllabi
            WHERE id = %s
            """,
            (syllabus_id,)
        )
        syllabus = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()

    if not syllabus:
        flash("Syllabus not found.")
        return redirect(url_for("manage_syllabi"))

    directory = os.path.dirname(syllabus["filepath"])
    filename = os.path.basename(syllabus["filepath"])

    return send_from_directory(
        directory,
        filename,
        as_attachment=False,
        download_name=syllabus["filename"]
    )


# =========================================================
# DOWNLOAD SYLLABUS (ADMIN)
# =========================================================

@app.route("/admin/syllabus/download/<int:syllabus_id>")
def admin_download_syllabus(syllabus_id):

    if not admin_required():
        flash("Admin login required.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("manage_syllabi"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                filepath,
                filename
            FROM syllabi
            WHERE id = %s
            """,
            (syllabus_id,)
        )
        syllabus = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()

    if not syllabus:
        flash("Syllabus not found.")
        return redirect(url_for("manage_syllabi"))

    directory = os.path.dirname(syllabus["filepath"])
    filename = os.path.basename(syllabus["filepath"])

    return send_from_directory(
        directory,
        filename,
        as_attachment=True,
        download_name=syllabus["filename"]
    )


# =========================================================
# DELETE SYLLABUS (ADMIN)
# =========================================================

@app.route("/admin/syllabus/<int:syllabus_id>/delete", methods=["POST"])
def delete_syllabus(syllabus_id):

    if not admin_required():
        flash("Admin login required.")
        return redirect(url_for("admin_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("manage_syllabi"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT filepath
            FROM syllabi
            WHERE id = %s
            """,
            (syllabus_id,)
        )
        syllabus = cursor.fetchone()

        if not syllabus:
            flash("Syllabus not found.")
            return redirect(url_for("manage_syllabi"))

        cursor.execute(
            """
            DELETE FROM syllabi
            WHERE id = %s
            """,
            (syllabus_id,)
        )
        connection.commit()

        try:
            if os.path.exists(syllabus["filepath"]):
                os.remove(syllabus["filepath"])
        except Exception as e:
            print("Syllabus file removal error:", e)

        flash("Syllabus deleted.", "success")

    except Exception as e:
        connection.rollback()
        print("Delete syllabus error:", e)
        flash("Unable to delete syllabus.")
    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("manage_syllabi"))


# =========================================================
# REGISTRATION - FACULTY + STUDENT
# =========================================================
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        role = request.form.get("role", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if role not in ("faculty", "student"):
            flash("Please select an account type.")
            return redirect(url_for("register"))

        if not password:
            flash("Please enter a password.")
            return redirect(url_for("register"))

        if not confirm_password:
            flash("Please confirm your password.")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.")
            return redirect(url_for("register"))

        email_field = "email" if role == "faculty" else "student_email"
        otp_email = normalize_email(request.form.get(email_field))
        verified_email = session.get("verified_email")
        verified_at = session.get("verified_email_at", 0)

        if (
            not otp_email
            or otp_email != verified_email
            or datetime.now().timestamp() - verified_at > OTP_VERIFIED_WINDOW
        ):
            flash("Please verify your email with OTP first.")
            return redirect(url_for("register"))

        connection = get_connection()

        if connection is None:
            flash("Database connection failed.")
            return redirect(url_for("register"))

        cursor = connection.cursor(dictionary=True)

        try:

            password_hash = generate_password_hash(password)

            if role == "faculty":

                name = request.form.get("name", "").strip()
                email = request.form.get("email", "").strip().lower()
                department = request.form.get("department", "").strip()
                year_of_experience_raw = request.form.get("year_of_experience", "0").strip()

                if not name:
                    flash("Please enter your full name.")
                    return redirect(url_for("register"))

                if not email:
                    flash("Please enter your email address.")
                    return redirect(url_for("register"))

                if not department:
                    flash("Please enter Department.")
                    return redirect(url_for("register"))

                try:
                    year_of_experience = int(year_of_experience_raw or 0)
                except ValueError:
                    flash("Year of experience must be a whole number.")
                    return redirect(url_for("register"))

                if year_of_experience < 0:
                    flash("Year of experience cannot be negative.")
                    return redirect(url_for("register"))

                cursor.execute(
                    """
                    SELECT faculty_id
                    FROM faculty
                    WHERE email = %s
                    """,
                    (email,)
                )

                if cursor.fetchone():
                    flash("Email is already registered.")
                    return redirect(url_for("register"))

                cursor.execute(
                    """
                    INSERT INTO faculty
                    (
                        name,
                        email,
                        department,
                        year_of_experience,
                        password,
                        status
                    )
                    VALUES
                    (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        name,
                        email,
                        department,
                        year_of_experience,
                        password_hash,
                        "Active"
                    )
                )

                connection.commit()

                session.pop("verified_email", None)
                session.pop("verified_email_at", None)

                flash("Faculty registration successful. Please login.", "success")

                return redirect(url_for("faculty_login"))

            uucms_number = request.form.get("uucms_number", "").strip()
            student_name = request.form.get("student_name", "").strip()
            student_email = request.form.get("student_email", "").strip().lower()
            raw_course_id = request.form.get("course_id", "").strip()
            course_id=normalize_course(raw_course_id)
            section_id = request.form.get("section_id", "").strip()
            academic_year = request.form.get("academic_year", "").strip()
            year_of_study = request.form.get("year_of_study", "").strip()

            if not uucms_number:
                flash("Please enter your UUCMS number.")
                return redirect(url_for("register"))

            if not student_name:
                flash("Please enter your name.")
                return redirect(url_for("register"))

            if not student_email:
                flash("Please enter your email address.")
                return redirect(url_for("register"))

            if not course_id:
                flash("Please enter Course ID.")
                return redirect(url_for("register"))

            if not section_id:
                flash("Please enter Section ID.")
                return redirect(url_for("register"))

            if not academic_year:
                flash("Please enter Academic Year.")
                return redirect(url_for("register"))

            if year_of_study not in ("1", "2", "3", "4"):
                flash("Please select your Year of Study.")
                return redirect(url_for("register"))

            cursor.execute(
                """
                SELECT student_id
                FROM student
                WHERE uucms_number = %s
                """,
                (uucms_number,)
            )

            if cursor.fetchone():
                flash("UUCMS number is already registered.")
                return redirect(url_for("register"))

            cursor.execute(
                """
                SELECT student_id
                FROM student
                WHERE email = %s
                """,
                (student_email,)
            )

            if cursor.fetchone():
                flash("Student email is already registered.")
                return redirect(url_for("register"))

            cursor.execute(
                """
                INSERT INTO student
                (
                    uucms_number,
                    name,
                    email,
                    password,
                    course_id,
                    section_id,
                    academic_year,
                    year_of_study
                )
                VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uucms_number,
                    student_name,
                    student_email,
                    password_hash,
                    course_id,
                    section_id,
                    academic_year,
                    int(year_of_study)
                )
            )

            connection.commit()

            session.pop("verified_email", None)
            session.pop("verified_email_at", None)

            flash("Student registration successful. Please login.", "success")

            return redirect(url_for("student_login"))

        except Exception as e:
            connection.rollback()
            print("Registration error:", e)
            flash("Registration failed. Check the terminal for details.")
            return redirect(url_for("register"))

        finally:
            cursor.close()
            connection.close()

    return render_template("register.html")


# =========================================================
# FACULTY LOGIN
# =========================================================

@app.route("/faculty/login", methods=["GET", "POST"])
def faculty_login():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        selected_role = request.form.get("role", "").strip().lower()

        if selected_role not in ("faculty", "hod"):
            flash("Please select a valid role.")
            return redirect(url_for("faculty_login"))

        if not email or not password:
            flash("Please enter email and password.")
            return redirect(url_for("faculty_login"))

        connection = get_connection()

        if connection is None:
            flash("Database connection failed.")
            return redirect(url_for("faculty_login"))

        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT
                    faculty_id,
                    name,
                    email,
                    department,
                    password,
                    status,
                    role
                FROM faculty
                WHERE email = %s
                """,
                (email,)
            )
            faculty = cursor.fetchone()
        finally:
            cursor.close()
            connection.close()

        if faculty is None:
            flash("Invalid email or password.")
            return redirect(url_for("faculty_login"))

        if (
            "status" in faculty
            and faculty["status"]
            and str(faculty["status"]).lower() != "active"
        ):
            flash("Your faculty account is not active.")
            return redirect(url_for("faculty_login"))

        try:
            password_correct = check_password_hash(faculty["password"], password)
        except Exception as e:
            print("Password verification error:", e)
            password_correct = False

        if not password_correct:
            flash("Invalid email or password.")
            return redirect(url_for("faculty_login"))

        database_role = str(faculty.get("role") or "faculty").strip().lower()

        if database_role not in ("faculty", "hod"):
            flash("Invalid account role. Please contact the administrator.")
            return redirect(url_for("faculty_login"))

        if selected_role != database_role:
            flash("The selected role does not match your account role.")
            return redirect(url_for("faculty_login"))

        session.clear()
        session["role"] = database_role
        session["faculty_role"] = database_role
        session["faculty_id"] = faculty["faculty_id"]
        session["faculty_email"] = faculty["email"]
        session["faculty_name"] = faculty["name"]
        session["department"] = faculty.get("department")

        if database_role == "hod":
            return redirect(url_for("hod_dashboard"))

        return redirect(url_for("faculty_dashboard"))

    return render_template("faculty_login.html")


# =========================================================
# FACULTY DASHBOARD
# =========================================================

@app.route("/faculty/dashboard")
def faculty_dashboard():

    if not faculty_required():
        flash("Please login as faculty.")
        return redirect(url_for("faculty_login"))

    faculty_id = session.get("faculty_id")

    if not faculty_id:
        session.clear()
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("faculty_login"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                faculty_id,
                name,
                email,
                department,
                status
            FROM faculty
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )
        faculty = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()

    if not faculty:
        session.clear()
        flash("Faculty account not found.")
        return redirect(url_for("faculty_login"))

    stats = {
        "papers_generated": 0,
        "syllabi_uploaded": 0,
        "total_questions": 0,
        "last_active": "—"
    }

    chart_data = {
        "months": [],
        "papers_per_month": [],
        "difficulty": {
            "Easy": 0,
            "Medium": 0,
            "Hard": 0
        }
    }

    stats_connection = get_connection()

    if stats_connection is not None:

        stats_cursor = stats_connection.cursor(dictionary=True)

        try:
            stats_cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM question_papers
                WHERE faculty_id = %s
                """,
                (faculty_id,)
            )
            stats["papers_generated"] = stats_cursor.fetchone()["total"] or 0

            stats_cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM syllabi
                WHERE faculty_id = %s
                """,
                (faculty_id,)
            )
            stats["syllabi_uploaded"] = stats_cursor.fetchone()["total"] or 0

            stats_cursor.execute(
                """
                SELECT COALESCE(SUM(num_questions), 0) AS total
                FROM question_papers
                WHERE faculty_id = %s
                """,
                (faculty_id,)
            )
            stats["total_questions"] = stats_cursor.fetchone()["total"] or 0

            stats_cursor.execute(
                """
                SELECT MAX(latest) AS last_active
                FROM (
                    SELECT MAX(created_at) AS latest
                    FROM question_papers
                    WHERE faculty_id = %s

                    UNION ALL

                    SELECT MAX(uploaded_at) AS latest
                    FROM syllabi
                    WHERE faculty_id = %s
                ) AS combined
                """,
                (faculty_id, faculty_id)
            )
            last_active_row = stats_cursor.fetchone()

            if last_active_row and last_active_row["last_active"]:
                stats["last_active"] = last_active_row["last_active"].strftime("%d %b %Y")

            stats_cursor.execute(
                """
                SELECT
                    DATE_FORMAT(created_at, '%Y-%m') AS month_key,
                    DATE_FORMAT(created_at, '%b') AS month_label,
                    COUNT(*) AS total
                FROM question_papers
                WHERE faculty_id = %s
                AND created_at >= DATE_SUB(NOW(), INTERVAL 6 MONTH)
                GROUP BY month_key, month_label
                ORDER BY month_key ASC
                """,
                (faculty_id,)
            )
            month_rows = stats_cursor.fetchall()

            if month_rows:
                chart_data["months"] = [row["month_label"] for row in month_rows]
                chart_data["papers_per_month"] = [row["total"] for row in month_rows]

            stats_cursor.execute(
                """
                SELECT
                    difficulty,
                    COUNT(*) AS total
                FROM question_papers
                WHERE faculty_id = %s
                GROUP BY difficulty
                """,
                (faculty_id,)
            )
            difficulty_rows = stats_cursor.fetchall()

            if difficulty_rows:
                difficulty_totals = {"Easy": 0, "Medium": 0, "Hard": 0}

                for row in difficulty_rows:
                    label = (row["difficulty"] or "").strip().capitalize()
                    if not label:
                        continue
                    difficulty_totals[label] = difficulty_totals.get(label, 0) + row["total"]

                chart_data["difficulty"] = difficulty_totals

        except Exception as e:
            print("Faculty dashboard stats error:", e)
        finally:
            stats_cursor.close()
            stats_connection.close()

    return render_template(
        "faculty_dashboard.html",
        faculty=faculty,
        stats=stats,
        chart_data=chart_data
    )


# ============================================================
# FACULTY ANALYTICS
# ============================================================

@app.route("/faculty/analytics")
def faculty_analytics():

    if not faculty_required():
        flash("Please login as faculty.")
        return redirect(url_for("faculty_login"))

    faculty_id = session.get("faculty_id")

    if not faculty_id:
        session.clear()
        return redirect(url_for("faculty_login"))

    try:
        selected_months = int(request.args.get("months", 6))
    except (TypeError, ValueError):
        selected_months = 6

    if selected_months not in (1, 3, 6, 12):
        selected_months = 6

    stats = {
        "papers_generated": 0,
        "syllabi_uploaded": 0,
        "total_questions": 0,
        "last_active": "—"
    }

    chart_data = {
        "months": [],
        "papers_per_month": [],

        "difficulty": {},
        "pattern": {},
        "subjects": {},

        "syllabus_months": [],
        "syllabus_per_month": []
    }

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("faculty_dashboard"))

    cursor = connection.cursor(dictionary=True)

    faculty = None

    try:

        cursor.execute(
            """
            SELECT
                faculty_id,
                name,
                email,
                department,
                status
            FROM faculty
            WHERE faculty_id = %s
            LIMIT 1
            """,
            (faculty_id,)
        )

        faculty = cursor.fetchone()

        if not faculty:
            session.clear()
            flash("Faculty account not found.", "error")
            return redirect(url_for("faculty_login"))

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total
            FROM question_papers
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )

        row = cursor.fetchone()
        stats["papers_generated"] = int(row["total"] or 0)

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total
            FROM syllabi
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )

        row = cursor.fetchone()
        stats["syllabi_uploaded"] = int(row["total"] or 0)

        cursor.execute(
            """
            SELECT
                COALESCE(
                    SUM(num_questions),
                    0
                ) AS total
            FROM question_papers
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )

        row = cursor.fetchone()
        stats["total_questions"] = int(row["total"] or 0)

        cursor.execute(
            """
            SELECT
                MAX(activity_date) AS last_active
            FROM
            (
                SELECT
                    MAX(created_at) AS activity_date
                FROM question_papers
                WHERE faculty_id = %s

                UNION ALL

                SELECT
                    MAX(uploaded_at) AS activity_date
                FROM syllabi
                WHERE faculty_id = %s
            ) AS activity
            """,
            (faculty_id, faculty_id)
        )

        row = cursor.fetchone()

        if row and row.get("last_active"):
            value = row["last_active"]
            if hasattr(value, "strftime"):
                stats["last_active"] = value.strftime("%d %b %Y")
            else:
                stats["last_active"] = str(value)

        now = datetime.now()

        current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        def shift_month(date_value, offset):
            month_index = date_value.year * 12 + (date_value.month - 1) + offset
            year = month_index // 12
            month = (month_index % 12) + 1
            return date_value.replace(year=year, month=month, day=1)

        month_starts = [
            shift_month(current_month, -offset)
            for offset in range(selected_months - 1, -1, -1)
        ]

        start_date = month_starts[0]
        end_date = shift_month(current_month, 1)

        chart_data["months"] = [month.strftime("%b %Y") for month in month_starts]
        chart_data["syllabus_months"] = [month.strftime("%b %Y") for month in month_starts]

        # NOTE: '%%Y-%%m' (doubled) because these queries pass parameters.
        cursor.execute(
            """
            SELECT
                DATE_FORMAT(
                    created_at,
                    '%Y-%m'
                ) AS month_key,

                COUNT(*) AS total

            FROM question_papers

            WHERE faculty_id = %s

              AND created_at >= %s

              AND created_at < %s

            GROUP BY
                DATE_FORMAT(
                    created_at,
                    '%Y-%m'
                )

            ORDER BY
                month_key ASC
            """,
            (faculty_id, start_date, end_date)
        )

        rows = cursor.fetchall()

        paper_by_month = {}

        for row in rows:
            key = row["month_key"]
            paper_by_month[key] = int(row["total"] or 0)

        chart_data["papers_per_month"] = [
            paper_by_month.get(month.strftime("%Y-%m"), 0)
            for month in month_starts
        ]

        cursor.execute(
            """
            SELECT
                difficulty,
                COUNT(*) AS total

            FROM question_papers

            WHERE faculty_id = %s

            GROUP BY difficulty

            ORDER BY total DESC
            """,
            (faculty_id,)
        )

        difficulty_data = {}

        for row in cursor.fetchall():
            value = row.get("difficulty")
            if value is None:
                continue
            label = str(value).strip()
            if not label:
                continue
            label = label.capitalize()
            difficulty_data[label] = difficulty_data.get(label, 0) + int(row["total"] or 0)

        chart_data["difficulty"] = difficulty_data

        cursor.execute(
            """
            SELECT
                pattern,
                COUNT(*) AS total

            FROM question_papers

            WHERE faculty_id = %s

            GROUP BY pattern

            ORDER BY total DESC
            """,
            (faculty_id,)
        )

        pattern_data = {}

        for row in cursor.fetchall():
            value = row.get("pattern")
            if value is None:
                continue
            label = str(value).strip()
            if not label:
                continue
            label = label.upper()
            pattern_data[label] = pattern_data.get(label, 0) + int(row["total"] or 0)

        chart_data["pattern"] = pattern_data

        cursor.execute(
            """
            SELECT
                qp.subject,
                s.subject_name,
                COUNT(*) AS total

            FROM question_papers qp

            LEFT JOIN subject s
                ON qp.subject = s.subject_id

            WHERE qp.faculty_id = %s

              AND qp.subject IS NOT NULL

              AND qp.subject <> ''

            GROUP BY
                qp.subject,
                s.subject_name

            ORDER BY
                total DESC

            LIMIT 10
            """,
            (faculty_id,)
        )

        subject_data = {}

        for row in cursor.fetchall():
            subject_name = row.get("subject_name")
            subject_id = row.get("subject")

            if subject_name:
                label = str(subject_name).strip()
            else:
                label = str(subject_id).strip()

            if not label:
                continue

            subject_data[label] = int(row["total"] or 0)

        chart_data["subjects"] = subject_data

        cursor.execute(
            """
            SELECT
                DATE_FORMAT(
                    uploaded_at,
                    '%Y-%m'
                ) AS month_key,

                COUNT(*) AS total

            FROM syllabi

            WHERE faculty_id = %s

              AND uploaded_at >= %s

              AND uploaded_at < %s

            GROUP BY
                DATE_FORMAT(
                    uploaded_at,
                    '%Y-%m'
                )

            ORDER BY
                month_key ASC
            """,
            (faculty_id, start_date, end_date)
        )

        rows = cursor.fetchall()

        syllabus_by_month = {}

        for row in rows:
            key = row["month_key"]
            syllabus_by_month[key] = int(row["total"] or 0)

        chart_data["syllabus_per_month"] = [
            syllabus_by_month.get(month.strftime("%Y-%m"), 0)
            for month in month_starts
        ]

    except Exception as e:
        print("Faculty analytics error:", repr(e))

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "faculty_analytics.html",
        faculty=faculty,
        stats=stats,
        chart_data=chart_data,
        selected_months=selected_months
    )


# =====================================================
# QUESTION PAPER HISTORY
# =====================================================

@app.route("/faculty/qp-history")
def qp_history():

    if not faculty_required():
        flash("Please login as faculty.")
        return redirect(url_for("faculty_login"))

    faculty_id = session.get("faculty_id")

    if not faculty_id:
        session.clear()
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("faculty_dashboard"))

    cursor = connection.cursor(dictionary=True)

    try:

        cursor.execute(
            """
            SELECT
                faculty_id,
                name,
                email,
                department,
                year_of_experience,
                status
            FROM faculty
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )

        faculty = cursor.fetchone()

        if not faculty:
            session.clear()
            flash("Faculty account not found.")
            return redirect(url_for("faculty_login"))

        cursor.execute(
            """
            SELECT
                id,
                faculty_id,
                subject,
                course,
                total_marks,
                difficulty,
                num_questions,
                pattern,
                created_at
            FROM question_papers
            WHERE faculty_id = %s
            ORDER BY created_at DESC, id DESC
            """,
            (faculty_id,)
        )

        question_papers = cursor.fetchall()

    except Exception as e:
        print("QP History Error:", e)
        flash("Unable to load question paper history.")
        question_papers = []

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "qp_history.html",
        faculty=faculty,
        question_papers=question_papers
    )

# =========================================================
# DELETE SAVED QUESTION PAPER (FACULTY)
# =========================================================

@app.route("/faculty/qp-history/<int:paper_id>/delete", methods=["POST"])
def faculty_delete_question_paper(paper_id):

    if not faculty_required():
        flash("Please login as faculty.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("qp_history"))

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            DELETE FROM question_papers
            WHERE id = %s
              AND faculty_id = %s
            """,
            (paper_id, session["faculty_id"])
        )
        connection.commit()
        flash("Question paper deleted.", "success")
    except Exception as e:
        connection.rollback()
        print("Faculty delete paper error:", e)
        flash("Unable to delete question paper.")
    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("qp_history"))


#-----------------------------------
#settings
#----------------------------------------
@app.route('/faculty/settings', methods=['GET', 'POST'])
def settings():

    if 'faculty_id' not in session:
        return redirect(url_for('faculty_login'))

    faculty_id = session['faculty_id']

    connection = get_connection()

    if connection is None:
        return "Database connection failed."

    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT *
        FROM faculty_settings
        WHERE faculty_id = %s
    """, (faculty_id,))

    settings_data = cursor.fetchone()

    if not settings_data:

        cursor.execute("""
            INSERT INTO faculty_settings (
                faculty_id,
                default_college,
                default_course,
                default_difficulty,
                default_pattern,
                default_num_questions,
                default_total_marks,
                theme,
                notify_paper_generated,
                notify_syllabus_failed,
                deletion_requested
            )
            VALUES (
                %s,
                'B.M.S College for Women',
                'BCA',
                'Medium',
                'Mixed',
                20,
                100,
                'dark',
                FALSE,
                FALSE,
                FALSE
            )
        """, (faculty_id,))

        connection.commit()

        cursor.execute("""
            SELECT *
            FROM faculty_settings
            WHERE faculty_id = %s
        """, (faculty_id,))

        settings_data = cursor.fetchone()

    if request.method == 'POST':

        form_type = request.form.get('form_type')

        if form_type == 'generation_defaults':

            default_college = request.form.get('default_college', 'B.M.S College for Women')
            default_course = request.form.get('default_course', 'BCA')
            default_difficulty = request.form.get('default_difficulty', 'Medium')
            default_pattern = request.form.get('default_pattern', 'Mixed')
            default_num_questions = request.form.get('default_num_questions', 20)
            default_total_marks = request.form.get('default_total_marks', 100)

            cursor.execute("""
                UPDATE faculty_settings
                SET
                    default_college = %s,
                    default_course = %s,
                    default_difficulty = %s,
                    default_pattern = %s,
                    default_num_questions = %s,
                    default_total_marks = %s
                WHERE faculty_id = %s
            """, (
                default_college,
                default_course,
                default_difficulty,
                default_pattern,
                default_num_questions,
                default_total_marks,
                faculty_id
            ))

            connection.commit()

            flash('Question paper defaults saved successfully.', 'success')

        elif form_type == 'appearance':

            theme = request.form.get('theme', 'dark')

            if theme not in ['dark', 'light']:
                theme = 'dark'

            cursor.execute("""
                UPDATE faculty_settings
                SET theme = %s
                WHERE faculty_id = %s
            """, (theme, faculty_id))

            connection.commit()

            flash('Appearance settings saved successfully.', 'success')

        elif form_type == 'notifications':

            notify_paper_generated = (1 if 'notify_paper_generated' in request.form else 0)
            notify_syllabus_failed = (1 if 'notify_syllabus_failed' in request.form else 0)

            cursor.execute("""
                UPDATE faculty_settings
                SET
                    notify_paper_generated = %s,
                    notify_syllabus_failed = %s
                WHERE faculty_id = %s
            """, (notify_paper_generated, notify_syllabus_failed, faculty_id))

            connection.commit()

            flash('Notification settings saved successfully.', 'success')

        elif form_type == 'request_deletion':

            cursor.execute("""
                UPDATE faculty_settings
                SET deletion_requested = TRUE
                WHERE faculty_id = %s
            """, (faculty_id,))

            connection.commit()

            flash('Account deletion request sent to the administrator.', 'success')

        cursor.close()
        connection.close()

        return redirect(url_for('settings'))

    cursor.execute("""
        SELECT *
        FROM faculty
        WHERE faculty_id = %s
    """, (faculty_id,))

    faculty = cursor.fetchone()

    cursor.close()
    connection.close()

    return render_template('settings.html', faculty=faculty, settings=settings_data)


# =========================================================
# FACULTY PROFILE
# =========================================================

@app.route("/faculty/profile", methods=["GET", "POST"])
def faculty_profile():

    if not faculty_required():
        flash("Please login as faculty.")
        return redirect(url_for("faculty_login"))

    faculty_id = session.get("faculty_id")

    if not faculty_id:
        session.clear()
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("faculty_dashboard"))

    cursor = connection.cursor(dictionary=True)

    if request.method == "POST":

        form_type = request.form.get("form_type", "")

        if form_type == "details":

            name = request.form.get("name", "").strip()
            department = request.form.get("department", "").strip()

            if not name:
                flash("Please enter your full name.")
                cursor.close()
                connection.close()
                return redirect(url_for("faculty_profile"))

            if not department:
                flash("Please enter your department.")
                cursor.close()
                connection.close()
                return redirect(url_for("faculty_profile"))

            try:
                cursor.execute(
                    """
                    UPDATE faculty
                    SET name = %s,
                        department = %s
                    WHERE faculty_id = %s
                    """,
                    (name, department, faculty_id)
                )
                connection.commit()
                session["faculty_name"] = name
                flash("Profile updated successfully.", "success")
            except Exception as e:
                connection.rollback()
                print("Faculty profile update error:", e)
                flash("Unable to update profile.")
            finally:
                cursor.close()
                connection.close()

            return redirect(url_for("faculty_profile"))

        elif form_type == "password":

            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            try:
                cursor.execute(
                    """
                    SELECT password
                    FROM faculty
                    WHERE faculty_id = %s
                    """,
                    (faculty_id,)
                )
                faculty_row = cursor.fetchone()

                if not faculty_row or not check_password_hash(faculty_row["password"], current_password):
                    flash("Current password is incorrect.")
                    return redirect(url_for("faculty_profile"))

                if not new_password:
                    flash("Please enter a new password.")
                    return redirect(url_for("faculty_profile"))

                if new_password != confirm_password:
                    flash("New passwords do not match.")
                    return redirect(url_for("faculty_profile"))

                if len(new_password) < 8:
                    flash("Password must contain at least 8 characters.")
                    return redirect(url_for("faculty_profile"))

                new_password_hash = generate_password_hash(new_password)

                cursor.execute(
                    """
                    UPDATE faculty
                    SET password = %s
                    WHERE faculty_id = %s
                    """,
                    (new_password_hash, faculty_id)
                )
                connection.commit()
                flash("Password changed successfully.", "success")

            except Exception as e:
                connection.rollback()
                print("Faculty password change error:", e)
                flash("Unable to change password.")
            finally:
                cursor.close()
                connection.close()

            return redirect(url_for("faculty_profile"))

        elif form_type == "photo":

            file = request.files.get("profile_image")

            if not file or file.filename == "":
                flash("Please choose an image to upload.")
                cursor.close()
                connection.close()
                return redirect(url_for("faculty_profile"))

            if not allowed_image_file(file.filename):
                flash("Only PNG or JPG images are allowed.")
                cursor.close()
                connection.close()
                return redirect(url_for("faculty_profile"))

            original_filename = secure_filename(file.filename)

            unique_name = (
                str(faculty_id) + "_" + str(int(datetime.now().timestamp())) + "_" + original_filename
            )

            filepath = os.path.join(app.config["PROFILE_UPLOAD_FOLDER"], unique_name)

            file.save(filepath)

            try:
                cursor.execute(
                    """
                    UPDATE faculty
                    SET profile_image = %s
                    WHERE faculty_id = %s
                    """,
                    (filepath, faculty_id)
                )
                connection.commit()
                flash("Profile picture updated successfully.", "success")
            except Exception as e:
                connection.rollback()
                print("Faculty profile picture update error:", e)
                flash("Unable to update profile picture.")
            finally:
                cursor.close()
                connection.close()

            return redirect(url_for("faculty_profile"))

    try:
        cursor.execute(
            """
            SELECT
                faculty_id,
                name,
                email,
                department,
                status,
                profile_image,
                created_at
            FROM faculty
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )
        faculty = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()

    if not faculty:
        session.clear()
        flash("Faculty account not found.")
        return redirect(url_for("faculty_login"))

    syllabus_count = 0
    paper_count = 0
    total_downloads = 0
    years_active = 0

    stats_connection = get_connection()

    if stats_connection is not None:

        stats_cursor = stats_connection.cursor()

        try:
            stats_cursor.execute(
                """
                SELECT COUNT(*)
                FROM syllabi
                WHERE faculty_id = %s
                """,
                (faculty_id,)
            )
            syllabus_count = stats_cursor.fetchone()[0]

            stats_cursor.execute(
                """
                SELECT COUNT(*)
                FROM question_papers
                WHERE faculty_id = %s
                """,
                (faculty_id,)
            )
            paper_count = stats_cursor.fetchone()[0]

            stats_cursor.execute(
                """
                SELECT COALESCE(SUM(download_count), 0)
                FROM syllabi
                WHERE faculty_id = %s
                """,
                (faculty_id,)
            )
            total_downloads = stats_cursor.fetchone()[0]

        except Exception as e:
            print("Faculty profile stats error:", e)
        finally:
            stats_cursor.close()
            stats_connection.close()

    created_at = faculty.get("created_at")

    if created_at:
        try:
            years_active = datetime.now().year - created_at.year
            if years_active < 0:
                years_active = 0
            if years_active == 0:
                years_active = "<1"
        except Exception:
            years_active = 0

    return render_template(
        "faculty_profile.html",
        faculty=faculty,
        syllabus_count=syllabus_count,
        paper_count=paper_count,
        total_downloads=total_downloads,
        years_active=years_active
    )


# =========================================================
# FACULTY PROFILE PICTURE (SERVE IMAGE)
# =========================================================

@app.route("/faculty/profile-image/<int:faculty_id>")
def faculty_profile_image(faculty_id):

    connection = get_connection()

    if connection is None:
        return "", 404

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT profile_image
            FROM faculty
            WHERE faculty_id = %s
            """,
            (faculty_id,)
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()

    if not row or not row["profile_image"]:
        return "", 404

    directory = os.path.dirname(row["profile_image"])
    filename = os.path.basename(row["profile_image"])

    return send_from_directory(directory, filename)


# =========================================================
# FORGOT PASSWORD  (link is EMAILED, never shown on screen)
# =========================================================

RESET_SENT_MESSAGE = (
    "If that email is registered, a password reset link has been sent."
)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Please enter your email.")
            return redirect(url_for("forgot_password"))

        connection = get_connection()

        if connection is None:
            flash("Database connection failed.")
            return redirect(url_for("forgot_password"))

        cursor = connection.cursor(dictionary=True)

        token = None

        try:
            cursor.execute(
                """
                SELECT faculty_id
                FROM faculty
                WHERE email = %s
                """,
                (email,)
            )
            faculty = cursor.fetchone()

            if faculty:

                token = secrets.token_urlsafe(32)
                expires_at = datetime.now() + timedelta(minutes=30)

                cursor.execute(
                    """
                    DELETE FROM password_resets
                    WHERE email = %s
                    AND role = %s
                    """,
                    (email, "faculty")
                )

                cursor.execute(
                    """
                    INSERT INTO password_resets
                    (email, role, token, expires_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (email, "faculty", token, expires_at)
                )

                connection.commit()

        except Exception as e:
            connection.rollback()
            print("Forgot password error:", e)
            flash("Unable to create password reset request.")
            return redirect(url_for("forgot_password"))
        finally:
            cursor.close()
            connection.close()

        if token:
            reset_link = url_for("reset_password", token=token, _external=True)
            send_reset_email(email, reset_link)

        # Same message whether or not the account exists.
        flash(RESET_SENT_MESSAGE, "success")
        return redirect(url_for("faculty_login"))

    return render_template("forgot_password.html")


# =========================================================
# ADMIN FORGOT PASSWORD
# =========================================================

@app.route("/admin/forgot-password", methods=["GET", "POST"])
def admin_forgot_password():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Please enter your admin email.")
            return redirect(url_for("admin_forgot_password"))

        connection = get_connection()

        if connection is None:
            flash("Database connection failed.")
            return redirect(url_for("admin_forgot_password"))

        cursor = connection.cursor(dictionary=True)

        token = None

        try:
            cursor.execute(
                """
                SELECT id
                FROM admin
                WHERE email = %s
                """,
                (email,)
            )
            admin = cursor.fetchone()

            if admin:

                token = secrets.token_urlsafe(32)
                expires_at = datetime.now() + timedelta(minutes=30)

                cursor.execute(
                    """
                    DELETE FROM password_resets
                    WHERE email = %s
                    AND role = %s
                    """,
                    (email, "admin")
                )

                cursor.execute(
                    """
                    INSERT INTO password_resets
                    (email, role, token, expires_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (email, "admin", token, expires_at)
                )

                connection.commit()

        except Exception as e:
            connection.rollback()
            print("Admin forgot password error:", e)
            flash("Unable to create password reset request.")
            return redirect(url_for("admin_forgot_password"))
        finally:
            cursor.close()
            connection.close()

        if token:
            reset_link = url_for("reset_password", token=token, _external=True)
            send_reset_email(email, reset_link)

        flash(RESET_SENT_MESSAGE, "success")
        return redirect(url_for("admin_login"))

    return render_template("admin_forgot_password.html")


# =========================================================
# RESET PASSWORD
# =========================================================

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("faculty_login"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT *
            FROM password_resets
            WHERE token = %s
            """,
            (token,)
        )
        reset_data = cursor.fetchone()

        if not reset_data:
            flash("Invalid or expired reset link.")
            return redirect(url_for("faculty_login"))

        expires_at = reset_data["expires_at"]

        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at)
            except ValueError:
                pass

        if datetime.now() > expires_at:
            cursor.execute(
                """
                DELETE FROM password_resets
                WHERE token = %s
                """,
                (token,)
            )
            connection.commit()
            flash("Reset link has expired.")
            return redirect(url_for("forgot_password"))

        if request.method == "POST":

            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not password:
                flash("Please enter a new password.")
                return redirect(url_for("reset_password", token=token))

            if password != confirm_password:
                flash("Passwords do not match.")
                return redirect(url_for("reset_password", token=token))

            if len(password) < 8:
                flash("Password must contain at least 8 characters.")
                return redirect(url_for("reset_password", token=token))

            password_hash = generate_password_hash(password)

            account_role = reset_data.get("role", "faculty")

            if account_role == "admin":
                cursor.execute(
                    """
                    UPDATE admin
                    SET password = %s
                    WHERE email = %s
                    """,
                    (password_hash, reset_data["email"])
                )
            else:
                cursor.execute(
                    """
                    UPDATE faculty
                    SET password = %s
                    WHERE email = %s
                    """,
                    (password_hash, reset_data["email"])
                )

            cursor.execute(
                """
                DELETE FROM password_resets
                WHERE token = %s
                """,
                (token,)
            )

            connection.commit()

            flash("Password reset successfully.", "success")

            if account_role == "admin":
                return redirect(url_for("admin_login"))

            return redirect(url_for("faculty_login"))

    except Exception as e:
        connection.rollback()
        print("Password reset error:", e)
        flash("Unable to reset password.")
        return redirect(url_for("forgot_password"))
    finally:
        cursor.close()
        connection.close()

    return render_template("reset_password.html", token=token)


# =========================================================
# UPLOAD SYLLABUS
# =========================================================

@app.route("/faculty/upload-syllabus", methods=["GET", "POST"])
@app.route("/faculty/upload_syllabus", methods=["GET", "POST"])
def upload_syllabus():

    if not faculty_required():
        flash("Please login as faculty.")
        return redirect(url_for("faculty_login"))

    faculty_id = session.get("faculty_id")

    if not faculty_id:
        session.clear()
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("faculty_dashboard"))

    cursor = connection.cursor(dictionary=True)

    subjects = []

    try:

        cursor.execute(
            """
            SELECT DISTINCT
                s.subject_id,
                s.subject_name,
                s.course_id,
                sf.academic_year,
                sf.section_id

            FROM subject_faculty sf

            INNER JOIN subject s
                ON sf.subject_id = s.subject_id

            WHERE sf.faculty_id = %s

            ORDER BY
                s.course_id,
                s.subject_id
            """,
            (faculty_id,)
        )

        subjects = cursor.fetchall()

        if request.method == "POST":

            subject_id = request.form.get("subject", "").strip()

            file = request.files.get("syllabus")

            if not subject_id:
                flash("Please select a subject.", "error")
                return redirect(url_for("upload_syllabus"))

            cursor.execute(
                """
                SELECT
                    sf.subject_id,
                    s.subject_name,
                    s.course_id

                FROM subject_faculty sf

                INNER JOIN subject s
                    ON sf.subject_id = s.subject_id

                WHERE sf.subject_id = %s
                AND sf.faculty_id = %s

                LIMIT 1
                """,
                (subject_id, faculty_id)
            )

            assigned_subject = cursor.fetchone()

            if not assigned_subject:
                flash("This subject is not assigned to you.", "error")
                return redirect(url_for("upload_syllabus"))

            if not file or file.filename == "":
                flash("Please select a PDF file.", "error")
                return redirect(url_for("upload_syllabus"))

            if not allowed_file(file.filename):
                flash("Only PDF files are allowed.", "error")
                return redirect(url_for("upload_syllabus"))

            original_filename = secure_filename(file.filename)

            unique_name = (
                str(faculty_id)
                + "_"
                + str(int(datetime.now().timestamp()))
                + "_"
                + original_filename
            )

            filepath = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)

            file.save(filepath)

            syllabus_text = ""

            try:
                reader = PdfReader(filepath)

                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        syllabus_text += (page_text + "\n")

            except Exception as e:
                print("PDF extraction error:", e)

            if not syllabus_text.strip():

                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                except Exception:
                    pass

                flash(
                    "Unable to extract text from this PDF. "
                    "Please upload a text-based PDF.",
                    "error"
                )

                return redirect(url_for("upload_syllabus"))

            cursor.execute(
                """
                INSERT INTO syllabi
                (
                    faculty_id,
                    subject,
                    filename,
                    filepath,
                    syllabus_text
                )

                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    faculty_id,
                    subject_id,
                    original_filename,
                    filepath,
                    syllabus_text
                )
            )

            connection.commit()

            flash("Syllabus uploaded successfully.", "success")

            return render_template(
                "syllabus_success.html",
                subject=assigned_subject["subject_name"]
            )

    except Exception as e:
        connection.rollback()
        print("Faculty syllabus error:", e)
        flash("Unable to load or upload syllabus.", "error")

    finally:
        cursor.close()
        connection.close()

    return render_template("upload_syllabus.html", subjects=subjects)


# =========================================================
# VIEW SYLLABI
# =========================================================

@app.route("/faculty/syllabus", defaults={"syllabus_id": None})
@app.route("/faculty/syllabus/<int:syllabus_id>")
def syllabus_view(syllabus_id):

    if not faculty_required():
        flash("Please login as faculty.")
        return redirect(url_for("faculty_login"))

    faculty_id = session.get("faculty_id")

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("faculty_dashboard"))

    cursor = connection.cursor(dictionary=True)

    try:
        if syllabus_id:

            cursor.execute(
                """
                SELECT
                    id,
                    subject,
                    filename,
                    uploaded_at
                FROM syllabi
                WHERE id = %s
                AND faculty_id = %s
                """,
                (syllabus_id, faculty_id)
            )
            syllabus = cursor.fetchone()

            if not syllabus:
                flash("Syllabus not found.")
                return redirect(url_for("syllabus_view"))

            syllabi = [syllabus]

        else:

            cursor.execute(
                """
                SELECT
                    id,
                    subject,
                    filename,
                    uploaded_at
                FROM syllabi
                WHERE faculty_id = %s
                ORDER BY uploaded_at DESC
                """,
                (faculty_id,)
            )
            syllabi = cursor.fetchall()

    finally:
        cursor.close()
        connection.close()

    return render_template("syllabus_view.html", syllabi=syllabi)


# =========================================================
# VIEW SYLLABUS PDF
# =========================================================

@app.route("/faculty/syllabus/view/<int:syllabus_id>")
def view_syllabus_pdf(syllabus_id):

    if not faculty_required():
        flash("Please login as faculty.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("syllabus_view"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                filepath,
                filename
            FROM syllabi
            WHERE id = %s
            AND faculty_id = %s
            """,
            (syllabus_id, session["faculty_id"])
        )
        syllabus = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()

    if not syllabus:
        flash("Syllabus not found.")
        return redirect(url_for("syllabus_view"))

    directory = os.path.dirname(syllabus["filepath"])
    filename = os.path.basename(syllabus["filepath"])

    return send_from_directory(
        directory,
        filename,
        as_attachment=False,
        download_name=syllabus["filename"]
    )


# =========================================================
# DOWNLOAD SYLLABUS
# =========================================================

@app.route("/faculty/syllabus/download/<int:syllabus_id>")
def download_syllabus(syllabus_id):

    if not faculty_required():
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("syllabus_view"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                filepath,
                filename
            FROM syllabi
            WHERE id = %s
            AND faculty_id = %s
            """,
            (syllabus_id, session["faculty_id"])
        )
        syllabus = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()

    if not syllabus:
        flash("Syllabus not found.")
        return redirect(url_for("syllabus_view"))

    count_connection = get_connection()

    if count_connection is not None:

        count_cursor = count_connection.cursor()

        try:
            count_cursor.execute(
                """
                UPDATE syllabi
                SET download_count = download_count + 1
                WHERE id = %s
                """,
                (syllabus_id,)
            )
            count_connection.commit()
        except Exception as e:
            count_connection.rollback()
            print("Download count update error:", e)
        finally:
            count_cursor.close()
            count_connection.close()

    directory = os.path.dirname(syllabus["filepath"])
    filename = os.path.basename(syllabus["filepath"])

    return send_from_directory(
        directory,
        filename,
        as_attachment=True,
        download_name=syllabus["filename"]
    )


# =========================================================
# EXTRACT TOPICS FROM SYLLABUS
# =========================================================

UNIT_HEADING_RE = re.compile(
    r"^(UNIT|MODULE|CHAPTER|PART|SECTION)\s*[-:]?\s*(\d+|[IVXLCDM]+)\b",
    re.IGNORECASE
)

STOP_SECTION_RE = re.compile(
    r"^(TEXT\s*BOOKS?|REFERENCE\s*BOOKS?|REFERENCES|"
    r"BIBLIOGRAPHY|SUGGESTED\s*READING|FURTHER\s*READING|"
    r"WEB\s*RESOURCES?|E-?RESOURCES?)\s*:?\s*$",
    re.IGNORECASE
)

HOURS_MARKER_RE = re.compile(
    r"^\[?\d+\s*hours?\]?\s*",
    re.IGNORECASE
)

HEADING_PREFIX_RE = re.compile(
    r"^(introduction to|overview of|what is|what are|"
    r"why use|why do we use|why|how to|how does|how|"
    r"types of|type of|applications of|application of|"
    r"advantages of|disadvantages of|limitations of|"
    r"challenges of|main challenges of|features of|"
    r"basics of|fundamentals of)\s+",
    re.IGNORECASE
)

IMPERATIVE_HEADING_RE = re.compile(
    r"^(look at|get|discover|prepare|select and train|select|"
    r"fine[- ]tune|present|launch|monitor|maintain|try it|explore|"
    r"obtain|gather|visualize|build|set up|choose|train a|"
    r"take a)\b",
    re.IGNORECASE
)

GENERIC_ONLY_TERMS = {
    "applications", "advantages", "limitations", "types",
    "features", "examples", "concepts", "introduction",
    "overview", "definition", "definitions", "techniques",
    "methods", "characteristics", "importance", "need",
    "objectives", "advantages and limitations", "uses"
}

PROBLEM_KEYWORDS = (
    "numerical", "numericals", "problem", "problems", "sums",
    "sum ", "calculation", "calculations", "calculate", "compute",
    "computation", "derive", "derivation", "probability"
)

FALLBACK_TOPICS = [
    "Fundamental concepts",
    "Important principles",
    "Applications",
    "Advanced concepts"
]


def reassemble_syllabus_sentences(text):

    raw_lines = [
        line.strip()
        for line in text.replace("\r", "\n").split("\n")
        if line.strip()
    ]

    blocks = []
    buffer = ""

    for line in raw_lines:

        if STOP_SECTION_RE.match(line.strip()):
            if buffer:
                blocks.append(buffer.strip())
                buffer = ""
            break

        if UNIT_HEADING_RE.match(line.upper()):
            if buffer:
                blocks.append(buffer.strip())
                buffer = ""
            blocks.append(line)
            continue

        buffer = (buffer + " " + line).strip() if buffer else line

        if buffer.endswith((".", "!", "?")):
            blocks.append(buffer.strip())
            buffer = ""

    if buffer:
        blocks.append(buffer.strip())

    return blocks


def _extract_topics_by_unit(syllabus, subject):

    ignored_headings = [
        "SYLLABUS",
        "COURSE CONTENT",
        "COURSE CONTENTS",
        "TABLE OF CONTENTS",
        "OBJECTIVES",
        "COURSE OBJECTIVES",
        "OUTCOMES",
        "COURSE OUTCOMES"
    ]

    blocks = reassemble_syllabus_sentences(syllabus)

    topics = []
    topic_units = []

    seen_first_unit_heading = False
    current_unit_index = -1

    for block in blocks:

        upper_block = block.upper()

        if UNIT_HEADING_RE.match(upper_block):
            seen_first_unit_heading = True
            current_unit_index += 1
            continue

        if upper_block in ignored_headings:
            continue

        if not seen_first_unit_heading:
            continue

        block = block.replace(":", ",")

        candidates = re.split(
            r"[,;]|(?<=[a-zA-Z])[.?!]\s+(?=[A-Z])",
            block
        )

        for candidate in candidates:

            item = candidate.strip()

            item = HOURS_MARKER_RE.sub("", item).strip()
            item = re.sub(r"^\d+\s*[.\)\-:]\s*", "", item).strip()
            item = re.sub(r"^[•●▪◦\-\*]\s*", "", item).strip()
            item = re.sub(r"^(and|or)\s+", "", item, flags=re.IGNORECASE).strip()
            item = item.rstrip(".!?").strip()

            normalized_item = HEADING_PREFIX_RE.sub("", item).strip()

            if len(normalized_item) >= 4:
                item = normalized_item

            if len(item) < 4:
                continue

            if len(item) > 100:
                continue

            letters = sum(character.isalpha() for character in item)

            if letters < 3:
                continue

            if item.count("(") != item.count(")"):
                continue

            if IMPERATIVE_HEADING_RE.match(item):
                continue

            if item.lower() in GENERIC_ONLY_TERMS:
                continue

            word_count = len(item.split())

            is_acronym_like = bool(re.match(r"^[A-Z0-9][A-Za-z0-9\-]{1,15}$", item))

            if word_count < 2 and not is_acronym_like:
                continue

            normalized_subject = " ".join(subject.lower().split())
            normalized_candidate = " ".join(item.lower().split())

            if normalized_candidate == normalized_subject:
                continue

            duplicate = False

            for existing_topic in topics:
                if existing_topic.lower() == item.lower():
                    duplicate = True
                    break

            if not duplicate:
                topics.append(item)
                topic_units.append(current_unit_index)

    topics_by_unit = {}

    for topic, unit_index in zip(topics, topic_units):
        topics_by_unit.setdefault(unit_index, []).append(topic)

    return topics_by_unit


def extract_topics_from_syllabus(syllabus, subject):

    if not syllabus:
        return [subject] + FALLBACK_TOPICS

    topics_by_unit = _extract_topics_by_unit(syllabus, subject)

    if not topics_by_unit:
        return [subject] + FALLBACK_TOPICS

    ordered_unit_indexes = sorted(topics_by_unit.keys())

    interleaved_topics = []

    max_topics_in_any_unit = max(len(unit_topics) for unit_topics in topics_by_unit.values())

    for position in range(max_topics_in_any_unit):
        for unit_index in ordered_unit_indexes:
            unit_topics = topics_by_unit[unit_index]
            if position < len(unit_topics):
                interleaved_topics.append(unit_topics[position])

    return interleaved_topics


class TopicPicker:

    def __init__(self, topics_by_unit):

        self._originals = {
            unit_index: list(unit_topics)
            for unit_index, unit_topics in topics_by_unit.items()
            if unit_topics
        }

        self._unit_indexes = list(self._originals.keys())

        self._queues = {unit_index: [] for unit_index in self._unit_indexes}

        self._unit_cycle = []

    def _refill_queue(self, unit_index):
        queue = list(self._originals[unit_index])
        random.shuffle(queue)
        self._queues[unit_index] = queue

    def _refill_unit_cycle(self):
        cycle = list(self._unit_indexes)
        random.shuffle(cycle)
        self._unit_cycle = cycle

    def next_topic(self):

        if not self._unit_indexes:
            return None

        if not self._unit_cycle:
            self._refill_unit_cycle()

        unit_index = self._unit_cycle.pop()

        if not self._queues[unit_index]:
            self._refill_queue(unit_index)

        return self._queues[unit_index].pop()

    def units(self):
        return list(self._unit_indexes)

    def next_topic_from_unit(self, unit_index):

        if unit_index not in self._queues:
            return None

        if not self._queues[unit_index]:
            self._refill_queue(unit_index)

        return self._queues[unit_index].pop()


def pick_unique_topic(picker, unit_index, used_topics, max_tries=30):

    topic = None

    for _ in range(max_tries):

        topic = picker.next_topic_from_unit(unit_index)

        if topic not in used_topics:
            used_topics.add(topic)
            return topic

    used_topics.add(topic)

    return topic


def allocate_evenly(items, number):

    if not items or number <= 0:
        return []

    item_list = list(items)

    base_count = number // len(item_list)
    remainder = number % len(item_list)

    counts = {item: base_count for item in item_list}

    remainder_items = random.sample(item_list, remainder) if remainder else []

    for item in remainder_items:
        counts[item] += 1

    sequence = []

    max_count = max(counts.values()) if counts else 0

    round_order = list(item_list)

    for _ in range(max_count):

        random.shuffle(round_order)

        for item in round_order:
            if counts[item] > 0:
                sequence.append(item)
                counts[item] -= 1

    return sequence


class TemplatePicker:

    def __init__(self, templates):
        self._templates = list(templates)
        self._queue = []

    def next_template(self):

        if not self._templates:
            return None

        if not self._queue:
            self._queue = list(self._templates)
            random.shuffle(self._queue)

        return self._queue.pop()


def build_topic_picker(
    syllabus,
    subject,
    max_words_per_topic=None,
    min_words_per_topic=None
):

    topics_by_unit = _extract_topics_by_unit(syllabus, subject)

    if not topics_by_unit:
        topics_by_unit = {0: [subject] + FALLBACK_TOPICS}

    if max_words_per_topic is not None:

        filtered_by_unit = {}

        for unit_index, unit_topics in topics_by_unit.items():

            short_topics = [
                topic for topic in unit_topics
                if len(topic.split()) <= max_words_per_topic
            ]

            filtered_by_unit[unit_index] = short_topics if short_topics else unit_topics

        topics_by_unit = filtered_by_unit

    if min_words_per_topic is not None:

        filtered_by_unit = {}

        for unit_index, unit_topics in topics_by_unit.items():

            deep_topics = [
                topic for topic in unit_topics
                if len(topic.split()) >= min_words_per_topic
            ]

            filtered_by_unit[unit_index] = deep_topics if deep_topics else unit_topics

        topics_by_unit = filtered_by_unit

    return TopicPicker(topics_by_unit)


def topics_are_usable(topics, subject):

    fallback_lower = {term.lower() for term in FALLBACK_TOPICS}

    for topic in topics:
        if topic == subject:
            continue
        if topic.lower() not in fallback_lower:
            return True

    return False


def extract_problem_topics(syllabus, subject):

    topics_by_unit = _extract_topics_by_unit(syllabus, subject)

    problem_topics = []

    for unit_topics in topics_by_unit.values():
        for topic in unit_topics:
            topic_lower = topic.lower()
            if any(keyword in topic_lower for keyword in PROBLEM_KEYWORDS):
                if topic not in problem_topics:
                    problem_topics.append(topic)

    return problem_topics


# =========================================================
# BLOOM'S TAXONOMY TEMPLATE BANKS
# =========================================================

# Part C mixes two formats: "split" = one 10-mark question made of two
# 5-mark parts (a)/(b); "single" = one direct 10-mark question.
# Question 1 is split, 2 is single, 3 split, ... Edit the list to change it.
PART_C_STYLE_CYCLE = ["split", "single"]


def part_c_style(index):
    return PART_C_STYLE_CYCLE[index % len(PART_C_STYLE_CYCLE)]


MIXED_SECTIONS = [
    {
        "name": "Part A",
        "marks_per_question": 2,
        "bloom_levels": ["Remember", "Understand"]
    },
    {
        "name": "Part B",
        "marks_per_question": 5,
        "bloom_levels": ["Understand", "Apply", "Analyze"]
    },
    {
        "name": "Part C",
        "marks_per_question": 10,
        "bloom_levels": ["Understand", "Apply", "Analyze", "Evaluate", "Create"]
    }
]


# Unit codes: 1-4 = that unit of the paper, 0 = "from any unit".
# "attempt" = answer any N; the number of questions GIVEN is len(units).
MIXED_PAPER_PLANS = {
    50: {
        "Part A": {"attempt": 10, "units": [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4]},
        "Part B": {"attempt": 4,  "units": [1, 2, 3, 4, 0]},
        "Part C": {"attempt": 1,  "units": [1, 0]},
    },
    60: {
        "Part A": {"attempt": 10, "units": [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4]},
        "Part B": {"attempt": 4,  "units": [1, 2, 3, 4, 0]},
        "Part C": {"attempt": 2,  "units": [1, 2, 0]},
    },
    70: {
        "Part A": {"attempt": 10, "units": [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4]},
        "Part B": {"attempt": 4,  "units": [1, 2, 3, 4, 0]},
        "Part C": {"attempt": 3,  "units": [1, 2, 3, 4]},
    },
    80: {
        "Part A": {"attempt": 10, "units": [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4]},
        "Part B": {"attempt": 4,  "units": [1, 2, 3, 4, 0]},
        "Part C": {"attempt": 4,  "units": [1, 2, 3, 4, 0]},
    },
    100: {
        "Part A": {"attempt": 10, "units": [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4]},
        "Part B": {"attempt": 6,  "units": [1, 1, 2, 2, 3, 3, 4, 0]},
        "Part C": {"attempt": 5,  "units": [1, 2, 3, 4, 0, 0]},
    },
}


def get_mixed_pattern(total_marks):
    """Returns {part: {"attempt", "given", "units"}} or None if unsupported."""

    try:
        total_marks = int(total_marks)
    except (TypeError, ValueError):
        return None

    plan = MIXED_PAPER_PLANS.get(total_marks)

    if not plan:
        return None

    return {
        part_name: {
            "attempt": part["attempt"],
            "given": len(part["units"]),
            "units": list(part["units"])
        }
        for part_name, part in plan.items()
    }


def get_mixed_section_counts(total_marks):

    pattern = get_mixed_pattern(total_marks)

    if not pattern:
        return None

    return {
        section_name: data["attempt"]
        for section_name, data in pattern.items()
    }


def get_mixed_given_counts(total_marks):

    pattern = get_mixed_pattern(total_marks)

    if not pattern:
        return None

    return {
        section_name: data["given"]
        for section_name, data in pattern.items()
    }


def mixed_unit_number(code, unit_count):
    """
    Table unit (1-4) -> real unit number 1..unit_count of this syllabus.
    0 ("from any unit") returns None. A syllabus with fewer or more than
    4 units simply wraps around (unit 4 of a 3-unit syllabus -> unit 1).
    """

    if not code:
        return None

    return ((int(code) - 1) % max(int(unit_count), 1)) + 1


def resolve_mixed_unit_index(code, unit_keys):
    """Table unit code -> a unit key of the topic picker (random for 'any')."""

    if not unit_keys:
        return 0

    if not code:
        return random.choice(unit_keys)

    return unit_keys[(int(code) - 1) % len(unit_keys)]


# =========================================================
# MARKS DISTRIBUTION HELPERS
# =========================================================

def compute_marks_distribution(total_marks, number):

    if number <= 0:
        return []

    base = total_marks // number
    remainder = total_marks % number

    marks_list = [base] * number

    for i in range(remainder):
        marks_list[i] += 1

    random.shuffle(marks_list)

    return marks_list


def distribute_weighted_marks(total_marks, section_question_counts, section_weights):

    section_count = len(section_question_counts)

    raw_shares = [
        count * weight
        for count, weight in zip(section_question_counts, section_weights)
    ]

    total_weight = sum(raw_shares)

    if total_weight <= 0:
        return [0] * section_count

    exact_shares = [(total_marks * raw) / total_weight for raw in raw_shares]

    floor_shares = [int(share) for share in exact_shares]

    remainder = total_marks - sum(floor_shares)

    fractional_order = sorted(
        (i for i in range(section_count) if section_question_counts[i] > 0),
        key=lambda i: exact_shares[i] - floor_shares[i],
        reverse=True
    )

    if fractional_order:
        for i in range(remainder):
            floor_shares[fractional_order[i % len(fractional_order)]] += 1

    return floor_shares


# =========================================================
# QUESTION GENERATION
# =========================================================

def format_bloom_question(template, topic):
    topic = (topic or "").strip()
    if topic and topic[0].islower():
        topic = topic[0].upper() + topic[1:]
    try:
        return template.format(topic=topic)
    except (KeyError, IndexError, ValueError):
        try:
            return template.format(topic)
        except Exception:
            return f"Explain {topic}."


MCQ_UNIT_STEMS = [
    "Which of the following topics is studied in the same unit as {anchor}?",
    "Which one of the following topics belongs to the same unit as {anchor}?",
    "{anchor} and which of the following topics are covered together in one unit?",
]


def build_unit_mcq(picker, unit_index, used_questions, used_correct):
    """
    Accurate template MCQ from the syllabus structure: the correct option is
    another topic of the SAME unit as the anchor topic named in the stem; the
    three distractors come from OTHER units. Returns
    (text, options, answer, unit_index) or None when the syllabus cannot
    support it (single unit, too few topics, or stems exhausted).
    """
    banks = picker._originals
    same = [t for t in banks.get(unit_index, []) if t]
    others = {
        u: [t for t in topics if t]
        for u, topics in banks.items() if u != unit_index
    }
    pool_other = [(u, t) for u, ts in others.items() for t in ts]

    if len(same) < 2 or len(pool_other) < 3:
        return None

    pairs = [(a, c) for a in same for c in same if a.lower() != c.lower()]
    random.shuffle(pairs)

    # Prefer correct answers not used yet; reuse one (with a new anchor) only
    # when every topic of the unit has already been an answer.
    pairs.sort(key=lambda pair: pair[1].lower() in used_correct)

    for anchor, correct in pairs:

        stems = list(MCQ_UNIT_STEMS)
        random.shuffle(stems)

        for stem in stems:
            text = stem.format(anchor=anchor)

            if text.lower() in used_questions:
                continue

            distractors = []
            seen = {anchor.lower(), correct.lower()}
            random.shuffle(pool_other)

            for _, t in pool_other:
                if t.lower() in seen:
                    continue
                seen.add(t.lower())
                distractors.append(t)
                if len(distractors) == 3:
                    break

            if len(distractors) < 3:
                return None

            options = [correct] + distractors
            random.shuffle(options)
            return text, options, correct

    return None


def sort_unit_wise(questions):
    """Unit-wise order inside each part (Parts keep their A, B, C order)."""
    def unit_no(q):
        m = re.search(r"\d+", str(q.get("unit", "")))
        return int(m.group()) if m else 0

    order = []
    for q in questions:
        if q.get("section") not in order:
            order.append(q.get("section"))

    result = []
    for sec in order:
        result.extend(sorted((q for q in questions if q.get("section") == sec), key=unit_no))
    return result


def create_questions(subject, syllabus, number, pattern, difficulty, total_marks):

    topic_picker = build_topic_picker(syllabus, subject)
    mcq_topic_picker = build_topic_picker(syllabus, subject, max_words_per_topic=8)
    deep_topic_picker = build_topic_picker(syllabus, subject, min_words_per_topic=2)

    questions = []

    used_questions = set()

    used_topics = set()

    # Curated banks written to read naturally for any syllabus topic and to
    # match the marks: Short = Part A style, Medium = Part B style,
    # Long = Part C style.
    def _flat(part, levels):
        bank = MIXED_TEMPLATES[part]
        return [t for lvl in levels for t in bank.get(lvl, [])]

    if difficulty.lower() == "easy":
        short_templates = _flat("Part A", ["Remember"])
        medium_pattern_templates_local = _flat("Part B", ["Understand"])
        long_templates = _flat("Part C", ["Understand", "Apply"])
    elif difficulty.lower() == "hard":
        short_templates = _flat("Part A", ["Understand"])
        medium_pattern_templates_local = _flat("Part B", ["Apply", "Analyze"])
        long_templates = _flat("Part C", ["Analyze", "Evaluate", "Create"])
    else:
        short_templates = _flat("Part A", ["Remember", "Understand"])
        medium_pattern_templates_local = _flat("Part B", ["Understand", "Apply", "Analyze"])
        long_templates = _flat("Part C", ["Understand", "Apply", "Analyze", "Evaluate"])

    if pattern == "Mixed":

        mixed_plan = get_mixed_pattern(total_marks)

        if not mixed_plan:
            raise ValueError(
                "Mixed pattern is supported for 50, 60, 70, 80 and 100 marks."
            )

        section_template_pickers = {
            part: {
                bloom: TemplatePicker(templates)
                for bloom, templates in by_bloom.items()
            }
            for part, by_bloom in MIXED_TEMPLATES.items()
        }

        for section_definition in MIXED_SECTIONS:

            section_name = section_definition["name"]
            section_marks = section_definition["marks_per_question"]
            section_plan = mixed_plan[section_name]
            section_count = section_plan["given"]
            unit_codes = section_plan["units"]
            bloom_levels = section_definition["bloom_levels"]

            section_topic_picker = (
                topic_picker if section_name == "Part A" else deep_topic_picker
            )

            unit_keys = sorted(section_topic_picker.units())

            bloom_template_pickers = section_template_pickers[section_name]

            section_bloom_sequence = allocate_evenly(bloom_levels, section_count)

            section_attempts = 0
            max_section_attempts = max(section_count * 40, 100)

            added_in_section = 0

            while added_in_section < section_count and section_attempts < max_section_attempts:

                section_attempts += 1

                unit_index = resolve_mixed_unit_index(
                    unit_codes[added_in_section], unit_keys
                )

                if section_name != "Part C" or part_c_style(added_in_section) == "single":

                    bloom_level = section_bloom_sequence[added_in_section]

                    topic = pick_unique_topic(section_topic_picker, unit_index, used_topics)

                    template = bloom_template_pickers[bloom_level].next_template()

                    question_text = format_bloom_question(template, topic)

                    question_key = question_text.strip().lower()

                    if question_key in used_questions:
                        continue

                    used_questions.add(question_key)

                    questions.append({
                        "text": question_text,
                        "marks": section_marks,
                        "section": section_name,
                        "bloom_level": bloom_level,
                        "unit": f"Unit {unit_index + 1}",
                        "subquestions": [],
                        "topics": [topic]
                    })


                else:

                    # Part C split: a 10-mark question made of two 5-mark parts,
                    # both from the unit this question is assigned to.
                    subquestions = []

                    part_b_pickers = section_template_pickers["Part B"]
                    sub_bloom_levels = random.sample(list(part_b_pickers), 2)

                    for sub_index in range(2):

                        sub_bloom = sub_bloom_levels[sub_index]
                        sub_topic = pick_unique_topic(section_topic_picker, unit_index, used_topics)
                        sub_template = part_b_pickers[sub_bloom].next_template()
                        sub_text = format_bloom_question(sub_template, sub_topic)
                        sub_key = sub_text.strip().lower()

                        retry = 0

                        while sub_key in used_questions and retry < 10:
                            retry += 1
                            sub_template = part_b_pickers[sub_bloom].next_template()
                            sub_topic = pick_unique_topic(section_topic_picker, unit_index, used_topics)
                            sub_text = format_bloom_question(sub_template, sub_topic)
                            sub_key = sub_text.strip().lower()

                        if sub_key in used_questions:
                            subquestions = []
                            break

                        used_questions.add(sub_key)

                        subquestions.append({
                            "text": sub_text,
                            "marks": 5,
                            "bloom_level": sub_bloom,
                            "unit": f"Unit {unit_index + 1}",
                            "topics": [sub_topic]
                        })

                    if len(subquestions) != 2:
                        continue

                    questions.append({
                        "text": "",
                        "marks": 10,
                        "section": section_name,
                        "bloom_level": "/".join(sub["bloom_level"] for sub in subquestions),
                        "unit": f"Unit {unit_index + 1}",
                        "subquestions": subquestions,
                        "topics": [t for sub in subquestions for t in sub.get("topics", [])]
                    })

                added_in_section += 1

            if added_in_section != section_count:
                raise ValueError(
                    f"Could not generate the required {section_count} "
                    f"questions for {section_name}."
                )

        calculated_total = sum(
            mixed_plan[section["name"]]["attempt"] * section["marks_per_question"]
            for section in MIXED_SECTIONS
        )

        if calculated_total != int(total_marks):
            raise ValueError(
                f"Mixed pattern total is {calculated_total}, "
                f"but {total_marks} marks were requested."
            )

        return sort_unit_wise(questions)

    if pattern in PATTERN_MARKS:
        marks_distribution = [PATTERN_MARKS[pattern]] * number
    else:
        marks_distribution = compute_marks_distribution(total_marks, number)

    mcq_template_picker = TemplatePicker(mcq_templates)
    short_template_picker = TemplatePicker(short_templates)
    medium_template_picker = TemplatePicker(medium_pattern_templates_local)
    long_template_picker = TemplatePicker(long_templates)

    if pattern == "MCQ":
        active_topic_picker = mcq_topic_picker
    elif pattern in ("Medium", "Long"):
        active_topic_picker = deep_topic_picker
    else:
        active_topic_picker = topic_picker

    # Unit-wise order: all Unit 1 questions first, then Unit 2, and so on.
    used_mcq_answers = set()

    unit_sequence = sorted(allocate_evenly(active_topic_picker.units(), number))

    attempts = 0
    max_attempts = max(number * 30, 100)

    while len(questions) < number and attempts < max_attempts:

        attempts += 1

        current_marks = marks_distribution[len(questions)]
        current_unit_index = unit_sequence[len(questions)]
        current_unit_label = f"Unit {current_unit_index + 1}"

        if pattern == "MCQ":

            unit_mcq = build_unit_mcq(
                mcq_topic_picker, current_unit_index, used_questions, used_mcq_answers
            )

            if unit_mcq:
                mcq_text, mcq_options, mcq_answer = unit_mcq
                used_questions.add(mcq_text.lower())
                used_mcq_answers.add(mcq_answer.lower())

                questions.append({
                    "text": mcq_text,
                    "options": mcq_options,
                    "answer": mcq_answer,
                    "marks": current_marks,
                    "unit": current_unit_label
                })
                continue

            topic = pick_unique_topic(mcq_topic_picker, current_unit_index, used_topics)
            template = mcq_template_picker.next_template()

            question_text = format_bloom_question(template, topic)
            question_key = question_text.lower()

            if question_key in used_questions:
                continue

            used_questions.add(question_key)

            # 4 distinct options: the real topic + 3 other topics as distractors
            # (no "All of these" / "None of these").
            options = [topic]
            guard = 0

            while len(options) < 4 and guard < 60:
                guard += 1
                other_unit = random.choice(mcq_topic_picker.units())
                candidate = mcq_topic_picker.next_topic_from_unit(other_unit)

                if candidate and candidate.strip().lower() not in {o.strip().lower() for o in options}:
                    options.append(candidate)

            if len(options) < 4:
                continue

            random.shuffle(options)

            questions.append({
                "text": question_text,
                "options": options,
                "answer": topic,
                "marks": current_marks,
                "unit": current_unit_label
            })

        elif pattern == "Short":

            topic = pick_unique_topic(topic_picker, current_unit_index, used_topics)
            template = short_template_picker.next_template()

            question_text = format_bloom_question(template, topic)
            question_key = question_text.lower()

            if question_key in used_questions:
                continue

            used_questions.add(question_key)

            questions.append({
                "text": question_text,
                "marks": current_marks,
                "unit": current_unit_label
            })

        elif pattern == "Medium":

            topic = pick_unique_topic(deep_topic_picker, current_unit_index, used_topics)
            template = medium_template_picker.next_template()

            question_text = format_bloom_question(template, topic)
            question_key = question_text.lower()

            if question_key in used_questions:
                continue

            used_questions.add(question_key)

            questions.append({
                "text": question_text,
                "marks": current_marks,
                "unit": current_unit_label
            })

        elif pattern == "Long":

            topic = pick_unique_topic(deep_topic_picker, current_unit_index, used_topics)
            template = long_template_picker.next_template()

            question_text = format_bloom_question(template, topic)
            question_key = question_text.lower()

            if question_key in used_questions:
                continue

            used_questions.add(question_key)

            questions.append({
                "text": question_text,
                "marks": current_marks,
                "unit": current_unit_label
            })

    return questions

# =========================================================
# SAVE GENERATED QUESTIONS TO QUESTION BANK
# =========================================================

def save_questions_to_bank(cursor, faculty_id, subject_id, difficulty, questions):
    """
    Copies every generated question into question_bank, once per academic
    year the faculty teaches this subject. Part C sub-questions are saved
    individually. Duplicate text for the same subject/faculty/year is skipped.
    Uses the caller's cursor, so it commits together with the paper.
    """

    cursor.execute(
        """
        SELECT DISTINCT academic_year
        FROM subject_faculty
        WHERE faculty_id = %s
          AND subject_id = %s
        """,
        (faculty_id, subject_id)
    )

    academic_years = [row[0] for row in cursor.fetchall()]

    if not academic_years:
        return 0

    items = []

    for question in questions:

        subquestions = question.get("subquestions") or []

        if subquestions:
            for sub in subquestions:
                items.append((sub.get("text", ""), sub.get("marks")))
        else:
            items.append((question.get("text", ""), question.get("marks")))

    saved = 0

    for text, marks in items:

        text = (text or "").strip()

        if not text or marks is None:
            continue

        for academic_year in academic_years:

            cursor.execute(
                """
                SELECT 1
                FROM question_bank
                WHERE subject_id = %s
                  AND faculty_id = %s
                  AND academic_year = %s
                  AND question_text = %s
                LIMIT 1
                """,
                (subject_id, faculty_id, academic_year, text)
            )

            if cursor.fetchone():
                continue

            cursor.execute(
                """
                INSERT INTO question_bank
                (
                    subject_id,
                    faculty_id,
                    academic_year,
                    question_text,
                    marks,
                    difficulty_level
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    subject_id,
                    faculty_id,
                    academic_year,
                    text,
                    int(marks),
                    difficulty
                )
            )

            saved += 1

    return saved


# =========================================================
# AI GENERATION  (Groq primary -> OpenRouter backup)
# =========================================================

GEMINI_MAX_TOPICS_PER_UNIT = 40

PATTERN_MARKS = {"MCQ": 1, "Short": 2, "Medium": 5, "Long": 10}


def get_pattern_counts(pattern, total_marks):
    """
    Question counts for MCQ / Short / Medium / Long papers (SEE 80, CIA 20).

    attempt = total_marks / marks per question
    given   = attempt + 1 (one internal choice); MCQ has no choice.
    e.g. SEE Short: 41 given, 40 to attempt. CIA Long: 3 given, 2 to attempt.
    """
    per_q = PATTERN_MARKS.get(pattern)

    if not per_q:
        return None

    attempt = int(total_marks) // per_q
    given = attempt if pattern == "MCQ" else attempt + 1

    return {"attempt": attempt, "given": given, "marks_each": per_q}

GEMINI_PATTERN_HINTS = {
    "MCQ": (
        "Each question is a multiple-choice question with exactly 4 distinct "
        "options and exactly one correct answer. Do not use 'All of these' or "
        "'None of these'. "
    ),
    "Short": (
        "Each question is a short-answer question answerable in 2-3 lines "
        "(definition, purpose, characteristics, simple distinction). "
    ),
    "Medium": (
        "Each question needs a one-page explanation (about 150-200 words): "
        "explanation with example, steps, comparison, or a short note. "
    ),
    "Long": (
        "Each question needs a detailed, structured answer: in-depth "
        "explanation, analysis, procedure, derivation, or design, as the "
        "topic supports. "
    ),
}

GEMINI_MIXED_SHAPE = (
    "JSON shape (use exactly these keys):\n"
    '{"part_a": [{"text": "...", "unit": 1, "topic": "...", "bloom_level": "Remember"}],\n'
    ' "part_b": [{"text": "...", "unit": 1, "topic": "...", "bloom_level": "Apply"}],\n'
    ' "part_c": [{"text": "...", "unit": 1, "topic": "...", "bloom_level": "Analyze"},\n'
    '            {"sub_questions": [\n'
    '     {"text": "...", "unit": 1, "topic": "...", "bloom_level": "Analyze"},\n'
    '     {"text": "...", "unit": 1, "topic": "...", "bloom_level": "Evaluate"}]}]}\n'
    '"unit" is the unit number as an integer. "topic" is the syllabus topic '
    "the question is based on.\n\n"
)


GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
# Free model ids on OpenRouter change often; override with OPENROUTER_MODELS.
OPENROUTER_MODELS = [
    m.strip()
    for m in os.getenv(
        "OPENROUTER_MODELS",
        "openai/gpt-oss-120b:free,"
        "meta-llama/llama-3.3-70b-instruct:free,"
        "google/gemma-3-27b-it:free"
    ).split(",")
    if m.strip()
]

AI_TIMEOUT_SECONDS = 120
AI_MAX_OUTPUT_TOKENS = 8000
AI_TEMPERATURE = 0.7
AI_RETRIES_PER_MODEL = 2


class AIProviderError(Exception):
    """Raised when every AI provider fails or returns unusable output."""


def _extract_json(text):
    """Parse JSON from a model reply, tolerating code fences and extra prose."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()

    try:
        return json.loads(text)
    except ValueError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            pass

    raise AIProviderError("The model returned invalid JSON.")


def _parse_chat_response(body):
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIProviderError("The model returned an empty or blocked response.")

    if not content:
        raise AIProviderError("The model returned an empty response.")

    return _extract_json(content)


def _build_providers():
    """Ordered list of (label, url, headers, model, extra_payload)."""
    providers = []

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        providers.append({
            "label": "Groq",
            "url": GROQ_API_URL,
            "headers": {
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json",
            },
            "models": [GROQ_MODEL],
            "extra": {"reasoning_effort": "medium"},
        })

    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        providers.append({
            "label": "OpenRouter",
            "url": OPENROUTER_API_URL,
            "headers": {
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("OPENROUTER_REFERER", "http://127.0.0.1:5000"),
                "X-Title": "QGEN-AI",
            },
            "models": OPENROUTER_MODELS,
            "extra": {},
        })

    return providers


def call_ai_json(prompt):
    """
    Send the prompt to Groq first; if Groq is unavailable, rate-limited or
    returns unusable output, fall back to each OpenRouter model in turn.
    Returns parsed JSON.
    """
    providers = _build_providers()

    if not providers:
        raise AIProviderError(
            "No AI provider is configured. Set GROQ_API_KEY and/or "
            "OPENROUTER_API_KEY in .env."
        )

    last_error = None

    for provider in providers:
        for model in provider["models"]:

            print(f"[AI] Trying {provider['label']} / {model}")

            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert university question-paper "
                            "setter. Reply with a single valid JSON object "
                            "and nothing else."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": AI_TEMPERATURE,
                "max_tokens": AI_MAX_OUTPUT_TOKENS,
                "response_format": {"type": "json_object"},
            }
            payload.update(provider["extra"])

            for attempt in range(AI_RETRIES_PER_MODEL):

                try:
                    response = requests.post(
                        provider["url"],
                        headers=provider["headers"],
                        json=payload,
                        timeout=AI_TIMEOUT_SECONDS,
                    )
                except requests.RequestException as e:
                    last_error = (
                        f"{provider['label']}/{model}: network error "
                        f"({type(e).__name__})"
                    )
                    time.sleep(2 * (attempt + 1))
                    continue

                if response.status_code == 200:
                    try:
                        result = _parse_chat_response(response.json())
                        print(f"[AI] Success with {provider['label']} / {model}")
                        return result
                    except (AIProviderError, ValueError) as e:
                        last_error = f"{provider['label']}/{model}: {e}"
                        print(f"[AI] {last_error}")
                        # Bad output: retry once, then try the next model
                        continue

                last_error = f"{provider['label']}/{model}: HTTP {response.status_code}"
                print(f"[AI] {last_error} {response.text[:300]}")

                # Temporary server trouble: retry the same model
                if response.status_code in (500, 502, 503, 504):
                    time.sleep(3 * (attempt + 1))
                    continue

                # 400 / 401 / 403 / 404 / 429: retrying won't help,
                # move on to the next model / provider
                break

    raise AIProviderError(f"All AI providers failed. Last error: {last_error}")


# ---- Backward-compatible names so the rest of app.py needs no changes ----
GeminiError = AIProviderError
call_gemini_json = call_ai_json

def _gemini_clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _gemini_units(syllabus, subject_name):
    """Returns {1: [topics], 2: [topics], ...} from the syllabus."""
    topics_by_unit = _extract_topics_by_unit(syllabus, subject_name)

    if not topics_by_unit:
        topics_by_unit = {0: [subject_name] + FALLBACK_TOPICS}

    units = {}

    for position, unit_index in enumerate(sorted(topics_by_unit)):
        units[position + 1] = topics_by_unit[unit_index][:GEMINI_MAX_TOPICS_PER_UNIT]

    return units


def _gemini_unit_label(value, unit_count, fallback_index):
    digits = re.sub(r"\D", "", str(value if value is not None else ""))
    number = int(digits) if digits else 0

    if not 1 <= number <= unit_count:
        number = (fallback_index % unit_count) + 1

    return f"Unit {number}"


def _gemini_bloom(value, allowed, index):
    label = _gemini_clean(value).capitalize()

    if label in allowed:
        return label

    return allowed[index % len(allowed)]

QUESTION_FORMATION_RULES = """
=========================================================
ACCURATE UNIVERSITY QUESTION FORMATION
=========================================================

You are setting a genuine undergraduate university examination.

The question must be formed from the ACTUAL ACADEMIC MEANING
of the syllabus topic.

IMPORTANT:
Do not take a topic name and simply attach a Bloom's Taxonomy
verb to it.

First identify:
1. What the topic actually teaches.
2. What students are expected to know or perform.
3. What type of academic question is appropriate.
4. What level of answer is justified by the marks.

Then formulate the question.

NOTE:
All examples below are only illustrations of question style.
Never reuse their topics unless the supplied syllabus contains them.

---------------------------------------------------------
GENERAL QUESTION FORMATION
---------------------------------------------------------

Every question must:

- be directly related to the supplied syllabus
- test one clearly identifiable academic concept or skill
- use standard university examination language
- be grammatically correct
- be precise and unambiguous
- be appropriate for undergraduate students
- match the marks assigned to it
- avoid unnecessary wording
- avoid artificial real-world scenarios
- avoid conversational language
- avoid AI-style wording
- avoid repeating the same concept
- avoid asking for information not present in the syllabus

Do NOT write questions merely by inserting a topic into a
generic template.

The topic itself must determine the question form.

---------------------------------------------------------
2-MARK QUESTION FORMATION
---------------------------------------------------------

A 2-mark question should normally require a SHORT,
DIRECT academic answer.

Suitable question types include:

1. Definition
Example:
"Define symmetric cryptography."

2. Identification
Example:
"What is a stream cipher?"

3. Purpose / function
Example:
"What is the purpose of a digital signature?"

4. Characteristics
Example:
"State any two characteristics of a block cipher."

5. Components
Example:
"List the basic components of the symmetric cipher model."

6. Principle
Example:
"State the principle of the Chinese Remainder Theorem."

7. Short distinction
Example:
"Differentiate between plaintext and ciphertext."

8. Simple concept explanation
Example:
"What is meant by key management?"

9. Formula / notation / syntax
Use only when the syllabus contains it.

10. Direct factual academic question
    Use only when the answer is explicitly supported
    by the syllabus.

A 2-mark question should generally be answerable in
approximately 1-4 concise statements.

DO NOT create unnecessarily broad 2-mark questions.

Avoid:
"Illustrate Network Security with a simple example."

Prefer:
"Define network security."

Avoid:
"Explain why Digital Signature Standard is important
in practice."

Prefer:
"What is the purpose of the Digital Signature Standard?"

---------------------------------------------------------
5-MARK QUESTION FORMATION
---------------------------------------------------------

A 5-mark question should require a meaningful,
structured academic answer.

Suitable forms include:

1. Explain a concept
"Explain the Symmetric Cipher Model."

2. Explain with a diagram
"Explain the structure of DES with a neat diagram."

3. Describe a process
"Describe the steps involved in the Diffie-Hellman
key exchange."

4. Explain working
"Explain the working of the RSA algorithm."

5. Compare two syllabus concepts
"Compare stream ciphers and block ciphers."

6. Differentiate between two concepts
"Differentiate between authentication and authorization."

7. Advantages and limitations
"Discuss the advantages and limitations of DES."

8. Short note
"Write a short note on message authentication codes."

9. Algorithm / procedure
Use only when the syllabus explicitly contains
an algorithm or procedure.

10. Numerical/problem
    Use only when the syllabus contains the required
    formula, method or procedure.

A 5-mark question should normally focus on ONE coherent
academic task.

If two concepts are requested, they must have a clear
academic relationship.

Avoid vague questions such as:

"Break down X into its key components and explain how
they interact."

Prefer:

"Explain the Symmetric Cipher Model with a neat diagram."

Avoid:

"Walk through the process of using DES to complete
a given task."

Prefer:

"Explain the working of DES."

Avoid:

"Describe the typical steps involved in Prime Numbers."

Prefer a question that matches what the syllabus actually
teaches about prime numbers, for example:

"Explain the properties of prime numbers."

ONLY use this example if those properties are present
in the supplied syllabus.

---------------------------------------------------------
10-MARK QUESTION FORMATION
---------------------------------------------------------

A 10-mark question must require a substantial,
well-structured undergraduate answer.

Suitable forms include:

1. Detailed explanation
"Explain the AES algorithm with its structure
and working."

2. Detailed process / algorithm
"Explain the RSA algorithm with key generation,
encryption and decryption."

3. Derivation
Use only where the syllabus contains the
required mathematical derivation.

4. Numerical problem
Use only where the syllabus supports the
required calculation.

5. Detailed comparison
Compare two clearly related syllabus concepts
with appropriate points.

6. Analysis
Use when the syllabus naturally supports
analysis.

7. Evaluation
Use only when there is a meaningful basis
for evaluation in the syllabus.

8. Design
Use only when designing or constructing
something is actually part of the subject.

9. Detailed procedure
Use for algorithms, protocols, methods or
processes when appropriate.

10. Structured discussion
    Use when a topic naturally contains several
    academic aspects that should be discussed.

Do NOT force "Design", "Evaluate", "Create",
"Critically evaluate", "Rank", or "Propose"
into every 10-mark question.

A 10-mark question must NOT become artificially
complex simply because it carries 10 marks.

---------------------------------------------------------
BLOOM'S TAXONOMY
---------------------------------------------------------

Bloom's Taxonomy is a QUESTION-QUALITY GUIDE,
not a question-writing template.

Use:

Remember:
- Define
- State
- List
- Identify
- Name

Understand:
- Explain
- Describe
- Summarize
- Discuss
- Illustrate

Apply:
- Solve
- Calculate
- Demonstrate
- Apply
- Use

Analyze:
- Analyze
- Compare
- Differentiate
- Examine

Evaluate:
- Justify
- Evaluate
- Assess
- Critically examine

Create:
- Design
- Develop
- Construct
- Formulate

BUT:

Never select a Bloom verb first and then attach
a topic to it.

Select the appropriate academic task first.

Then use a natural verb if required.

---------------------------------------------------------
AVOID ARTIFICIAL QUESTION PATTERNS
---------------------------------------------------------

NEVER use these patterns unless specifically required
by the syllabus:

"How can X address a common challenge?"

"How can X improve system performance?"

"How would X be implemented in a real project?"

"Demonstrate how X can be applied to solve a practical problem."

"Propose a new approach inspired by X."

"Rank the risks associated with X."

"Break down X into its key components."

"Walk through X to complete a given task."

"Identify situations where X is most effective."

"Critically evaluate X compared to alternative approaches."

These formulations often create artificial questions.

Instead, use the actual academic nature of the topic.

---------------------------------------------------------
SUBJECT-SPECIFIC ADAPTATION
---------------------------------------------------------

For THEORY subjects:
Prefer:
- Define
- Explain
- Describe
- Discuss
- Compare
- Differentiate
- Analyze

For PROGRAMMING subjects:
Use:
- Write a program
- Explain the algorithm
- Trace the program
- Predict the output
- Implement
ONLY when programming is part of the syllabus.

For MATHEMATICS:
Use:
- Solve
- Calculate
- Derive
- Prove
- Find
ONLY when supported by the syllabus.

For STATISTICS:
Use:
- Calculate
- Interpret
- Analyze
- Find
- Explain
ONLY when the required statistical method is present.

For DATABASES:
Use:
- Explain
- Design
- Write SQL query
- Normalize
- Compare
ONLY when supported by the syllabus.

For NETWORKING:
Use:
- Explain
- Describe
- Compare
- Illustrate
- Explain the working of
- Analyze
where academically appropriate.

For AI / ML:
Use:
- Explain
- Compare
- Calculate
- Apply
- Analyze
- Interpret
depending on the actual syllabus.

For MANAGEMENT / THEORY subjects:
Prefer:
- Define
- Explain
- Discuss
- Describe
- Compare
- Analyze
- Justify
where appropriate.

---------------------------------------------------------
COMPARISON QUESTIONS
---------------------------------------------------------

When using:

"Compare..."
or
"Differentiate between..."

BOTH concepts must be explicitly identified.

Bad:
"Differentiate between related techniques."

Good:
"Differentiate between stream ciphers and block ciphers."

The two concepts must:
- belong to the syllabus
- be meaningfully comparable
- have sufficient differences to form an answer.

---------------------------------------------------------
MULTI-CONCEPT QUESTIONS
---------------------------------------------------------

Do not combine unrelated topics merely to increase
the question difficulty.

Bad:
"Explain network security, RSA, hash functions and
Kerberos."

Good:
"Explain the working of RSA with suitable steps."

If two concepts are combined, they must have a
natural academic relationship.

---------------------------------------------------------
EXAMPLE RULE
---------------------------------------------------------

Use examples ONLY when:

1. the syllabus supports the concept;
2. an example genuinely improves understanding; and
3. the example is academically appropriate.

Do not invent business, industry, project,
application or real-world scenarios.

---------------------------------------------------------
FINAL QUESTION CHECK
---------------------------------------------------------

Before returning each question, internally ask:

1. Is this topic actually in the syllabus?
2. Does the question test the topic itself?
3. Is the wording natural for a university exam?
4. Is the question grammatically correct?
5. Is the question appropriate for the marks?
6. Is the Bloom level appropriate?
7. Is the question unnecessarily complicated?
8. Does it introduce outside information?
9. Does it repeat another question?
10. Could a university Assistant Professor realistically
    put this question directly into an examination paper?

If the answer to any of these is NO, rewrite the question.

Return only the JSON described below. Each "text" field must contain
only the final examination question, with no numbering or commentary.
"""


def _gemini_header(subject_name, difficulty, units):
    """
    Create a subject-independent prompt for UG university
    question-paper generation.
    """

    units_text = "\n".join(
        f"Unit {number}: " + "; ".join(topics)
        for number, topics in units.items()
    )

    level = (difficulty or "").strip().lower()

    if level == "easy":
        difficulty_hint = (
            "Easy level: focus mainly on remembering, understanding, "
            "basic concepts, definitions, principles and straightforward applications."
        )
    elif level == "hard":
        difficulty_hint = (
            "Hard level: focus mainly on application, analysis, evaluation, "
            "problem solving, comparison, interpretation and multi-step reasoning."
        )
    else:
        difficulty_hint = (
            "Medium level: use a balanced combination of understanding, "
            "application and moderate analysis."
        )

    return (
        "You are an experienced UG Assistant Professor and university "
        "question-paper setter.\n\n"
        f"Subject: {subject_name}\n"
        f"Difficulty Level: {difficulty_hint}\n\n"
        "Your task is to generate high-quality undergraduate university "
        "examination questions based strictly on the provided syllabus.\n\n"
        "SYLLABUS:\n"
        f"{units_text}\n\n"
        + QUESTION_FORMATION_RULES
    )
def _gemini_simple_entry(item, section, unit_count, index, seen):
    text = _gemini_clean(item.get("text"))
    key = text.lower()

    if len(text) < 10 or key in seen:
        return None

    topic = _gemini_clean(item.get("topic"))

    seen.add(key)

    return {
        "text": text,
        "marks": section["marks_per_question"],
        "section": section["name"],
        "bloom_level": _gemini_bloom(item.get("bloom_level"), section["bloom_levels"], index),
        "unit": _gemini_unit_label(item.get("unit"), unit_count, index),
        "subquestions": [],
        "topics": [topic] if topic else []
    }


def _gemini_part_c_entry(item, section, unit_count, index, seen):
    raw_subs = item.get("sub_questions")

    if not isinstance(raw_subs, list) or len(raw_subs) < 2:
        return None

    subquestions = []
    keys = []

    for sub_index, sub in enumerate(raw_subs[:2]):

        if not isinstance(sub, dict):
            return None

        text = _gemini_clean(sub.get("text"))
        key = text.lower()

        if len(text) < 10 or key in seen or key in keys:
            return None

        keys.append(key)
        topic = _gemini_clean(sub.get("topic"))

        subquestions.append({
            "text": text,
            "marks": 5,
            "bloom_level": _gemini_bloom(
                sub.get("bloom_level"), section["bloom_levels"], index + sub_index
            ),
            "unit": _gemini_unit_label(sub.get("unit"), unit_count, index + sub_index),
            "topics": [topic] if topic else []
        })

    seen.update(keys)

    return {
        "text": "",
        "marks": 10,
        "section": section["name"],
        "bloom_level": "/".join(sub["bloom_level"] for sub in subquestions),
        "unit": subquestions[0]["unit"],
        "subquestions": subquestions,
        "topics": [t for sub in subquestions for t in sub["topics"]]
    }


def _gemini_mixed_questions(header, unit_count, total_marks):

    plan = get_mixed_pattern(total_marks)

    if not plan:
        raise GeminiError("the Mixed pattern is not available for these marks")

    def unit_order(part_name):
        numbers = [
            mixed_unit_number(code, unit_count)
            for code in plan[part_name]["units"]
        ]
        return ", ".join(str(number) if number else "any" for number in numbers)

    prompt = (
        header
        + GEMINI_MIXED_SHAPE
        + f"Part A: exactly {plan['Part A']['given']} questions of 2 marks each. "
          "One-line answers: Define, State, List, Differentiate, What is, Write the "
          "syntax or formula. bloom_level is Remember or Understand (use both). "
          f"The unit of each question, in order, must be: {unit_order('Part A')}.\n"
        + f"Part B: exactly {plan['Part B']['given']} questions of 5 marks each. "
          "Each needs a structured answer of about 150-200 words, in the form "
          "the topic naturally calls for (explain a concept, explain with a "
          "diagram, describe a process, explain the working, compare two named "
          "concepts, advantages and limitations, or a short note). "
          "bloom_level is the level that honestly describes the question "
          "(Understand, Apply or Analyze). Do not force a level the topic "
          "does not support. "
          f"The unit of each question, in order, must be: {unit_order('Part B')}.\n"
        + f"Part C: exactly {plan['Part C']['given']} items worth 10 marks each, "
          "in two formats, in this order: "
          + ", ".join(
              f"{i + 1}={part_c_style(i)}" for i in range(plan['Part C']['given'])
          )
          + ". A 'split' item has exactly 2 sub_questions of 5 marks each "
          "(both from the SAME unit, different topics, independent of each other). "
          "A 'single' item is ONE direct descriptive question of 10 marks, with "
          "\"text\" and no sub_questions, needing a detailed answer of about "
          "300-400 words. Questions are substantial and descriptive, in the form "
          "the topic naturally calls for (detailed explanation, working or "
          "algorithm, detailed comparison of two named concepts, or structured "
          "discussion). Use Design, Derive, Evaluate or Write a program ONLY if "
          "the syllabus topic itself involves that. bloom_level is Understand, "
          "Apply, Analyze, Evaluate or Create, whichever truly fits. "
          f"The unit of each item, in order, must be: {unit_order('Part C')}.\n"
        + "Where a unit is given as 'any', choose the unit yourself, preferring a "
          "unit that is under-represented so far. In every question, set \"unit\" "
          "to the unit number the question belongs to.\n"
    )

    data = call_gemini_json(prompt)

    if not isinstance(data, dict):
        raise GeminiError("Gemini returned an unexpected structure")

    keys_by_part = {"Part A": "part_a", "Part B": "part_b", "Part C": "part_c"}

    questions = []
    seen = set()

    for section in MIXED_SECTIONS:

        name = section["name"]
        needed = plan[name]["given"]
        items = data.get(keys_by_part[name])

        if not isinstance(items, list):
            raise GeminiError(f"Gemini's answer is missing {name}")

        collected = []

        for item in items:

            if len(collected) == needed:
                break

            if not isinstance(item, dict):
                continue

            if name == "Part C" and item.get("sub_questions"):
                entry = _gemini_part_c_entry(item, section, unit_count, len(collected), seen)
            else:
                entry = _gemini_simple_entry(item, section, unit_count, len(collected), seen)

            if entry:
                collected.append(entry)

        if len(collected) < needed:
            raise GeminiError(
                f"Gemini gave only {len(collected)} usable questions for "
                f"{name} (needed {needed})"
            )

        # Enforce the unit order from the pattern table (keep Gemini's unit
        # only where the table says "from any unit").
        for position, entry in enumerate(collected):

            number = mixed_unit_number(plan[name]["units"][position], unit_count)

            if number:
                label = f"Unit {number}"
                entry["unit"] = label

                for sub in entry.get("subquestions") or []:
                    sub["unit"] = label

        questions.extend(collected)

    return sort_unit_wise(questions)


def _gemini_standard_questions(header, unit_count, number, pattern, total_marks):

    fixed_marks = PATTERN_MARKS.get(pattern)

    marks_distribution = (
        [fixed_marks] * number if fixed_marks
        else compute_marks_distribution(total_marks, number)
    )

    if pattern == "MCQ":
        shape = (
            'JSON shape:\n'
            '{"questions": [{"text": "...", "unit": 1, '
            '"options": ["...", "...", "...", "..."], "answer": 0}]}\n'
            '"answer" is the 0-based index of the correct option.\n\n'
        )
    else:
        shape = (
            'JSON shape:\n'
            '{"questions": [{"text": "...", "unit": 1}]}\n\n'
        )

    prompt = (
        header
        + shape
        + f"Write exactly {number} questions. "
        + GEMINI_PATTERN_HINTS.get(pattern, "")
        + "\n"
    )

    if pattern != "MCQ":
        prompt += (
            f"Every question carries exactly {marks_distribution[0]} marks. "
            "Match the depth of each question to those marks, and keep all "
            "questions at the same level of depth.\n"
        )

    data = call_gemini_json(prompt)

    items = data.get("questions") if isinstance(data, dict) else data

    if not isinstance(items, list):
        raise GeminiError("Gemini returned an unexpected structure")

    questions = []
    seen = set()

    for item in items:

        if len(questions) == number:
            break

        if not isinstance(item, dict):
            continue

        text = _gemini_clean(item.get("text"))
        key = text.lower()

        if len(text) < 10 or key in seen:
            continue

        entry = {
            "text": text,
            "marks": marks_distribution[len(questions)],
            "unit": _gemini_unit_label(item.get("unit"), unit_count, len(questions))
        }

        if pattern == "MCQ":

            raw_options = item.get("options")

            if not isinstance(raw_options, list):
                continue

            options = [
                re.sub(r"^[A-Da-d][\).:\-]\s+", "", _gemini_clean(option))
                for option in raw_options
            ]

            if (
                len(options) != 4
                or not all(options)
                or len({option.lower() for option in options}) != 4
            ):
                continue

            entry["options"] = options

            answer = item.get("answer")

            if isinstance(answer, int) and not isinstance(answer, bool) and 0 <= answer < 4:
                entry["answer"] = options[answer]

        seen.add(key)
        questions.append(entry)

    if len(questions) < number:
        raise GeminiError(
            f"Gemini gave only {len(questions)} usable questions (needed {number})"
        )

    # Unit-wise order (Unit 1 first ... last unit); stable, so ties keep order.
    def _unit_key(entry):
        m = re.search(r"\d+", str(entry.get("unit", "")))
        return int(m.group()) if m else 0

    questions.sort(key=_unit_key)

    return questions


# =========================================================
# VALIDATION (used by both AI mode and template mode)
# =========================================================

def validate_generated_questions(questions, pattern, total_marks):
    """
    Checks a generated paper against the SEE/CIA rules and returns a list of
    problems (empty list = OK).

    MCQ/Short/Medium/Long: given = attempt + 1 (MCQ: given = attempt), each
    question carries the fixed marks for the pattern, MCQ has 4 distinct
    options. Mixed: per-part given counts, marks per part, Part C = 2 x 5.
    """
    problems = []
    total_marks = int(total_marks)
    questions = questions or []

    def check_text(q, label):
        if not (q.get("text") or "").strip():
            problems.append(f"{label} has no question text.")

    if pattern == "Mixed":
        given = get_mixed_given_counts(total_marks)
        attempt = get_mixed_section_counts(total_marks)

        if not given:
            return ["Mixed pattern supports only 50, 60, 70, 80 and 100 marks."]

        part_marks = {s["name"]: s["marks_per_question"] for s in MIXED_SECTIONS}
        seen = set()

        for part, need in given.items():
            part_qs = [q for q in questions if q.get("section") == part]

            if len(part_qs) != need:
                problems.append(f"{part}: expected {need} questions, got {len(part_qs)}.")

            for i, q in enumerate(part_qs, 1):
                label = f"{part} Q{i}"

                if q.get("marks") != part_marks[part]:
                    problems.append(f"{label}: should carry {part_marks[part]} marks.")

                if part == "Part C":
                    # Part C is either split (a)+(b) of 5 marks each, or one
                    # direct 10-mark question.
                    subs = q.get("subquestions") or []
                    if subs:
                        if len(subs) != 2 or any(s.get("marks") != 5 for s in subs):
                            problems.append(f"{label}: split question must have two 5-mark parts (a, b).")
                        texts = subs
                    else:
                        texts = [q]
                    for s in texts:
                        check_text(s, label)
                        key = (s.get("text") or "").strip().lower()
                        if key in seen:
                            problems.append(f"{label}: duplicate question.")
                        seen.add(key)
                else:
                    check_text(q, label)
                    key = (q.get("text") or "").strip().lower()
                    if key in seen:
                        problems.append(f"{label}: duplicate question.")
                    seen.add(key)

        if len(questions) != sum(given.values()):
            problems.append(f"Expected {sum(given.values())} questions, got {len(questions)}.")

        if sum(attempt[p] * part_marks[p] for p in attempt) != total_marks:
            problems.append("Attempt counts do not add up to the total marks.")

        return problems

    counts = get_pattern_counts(pattern, total_marks)

    if not counts:
        return [f"Unknown pattern: {pattern}"]

    if len(questions) != counts["given"]:
        problems.append(
            f"{pattern}: expected {counts['given']} questions "
            f"(attempt {counts['attempt']}), got {len(questions)}."
        )

    seen = set()

    for i, q in enumerate(questions, 1):
        label = f"Q{i}"
        check_text(q, label)

        if q.get("marks") != counts["marks_each"]:
            problems.append(f"{label}: should carry {counts['marks_each']} mark(s).")

        key = (q.get("text") or "").strip().lower()
        if key in seen:
            problems.append(f"{label}: duplicate question.")
        seen.add(key)

        if pattern == "MCQ":
            opts = q.get("options") or []
            lowered = [str(o).strip().lower() for o in opts]
            if (
                len(opts) != 4
                or len(set(lowered)) != 4
                or any(not o for o in lowered)
                or any(o in ("all of these", "none of these", "all of the above", "none of the above") for o in lowered)
            ):
                problems.append(f"{label}: needs 4 distinct options (no all/none of these).")
        elif q.get("options"):
            problems.append(f"{label}: {pattern} questions must not have options.")

    return problems


def create_questions_gemini(subject_name, syllabus, number, pattern, difficulty, total_marks):
    """Same output structure as create_questions(), written by Gemini."""

    units = _gemini_units(syllabus, subject_name)
    unit_count = len(units)

    header = _gemini_header(subject_name, difficulty, units)

    if pattern == "Mixed":
        return _gemini_mixed_questions(header, unit_count, total_marks)

    return _gemini_standard_questions(header, unit_count, number, pattern, total_marks)



# =========================================================
# GENERATE QUESTION PAPER
# =========================================================
@app.route("/faculty/generate", methods=["GET", "POST"])
def generate_question_paper():

    if not faculty_required():
        flash("Please login as faculty.")
        return redirect(url_for("faculty_login"))

    if request.method == "GET":

        connection = get_connection()

        subjects = []

        if connection:

            cursor = connection.cursor(dictionary=True)

            try:
                cursor.execute(
                    """
                    SELECT DISTINCT
                        s.subject_id,
                        s.subject_name,
                        s.course_id
                    FROM subject_faculty sf

                    INNER JOIN subject s
                        ON sf.subject_id = s.subject_id

                    WHERE sf.faculty_id = %s

                    ORDER BY
                        s.course_id ASC,
                        s.subject_id ASC
                    """,
                    (session["faculty_id"],)
                )

                subjects = cursor.fetchall()

            except Exception as e:
                print("Error loading assigned subjects:", e)

            finally:
                cursor.close()
                connection.close()

        return render_template("generate_question_paper.html", subjects=subjects)

    subject_id = request.form.get("subject_id", "").strip()
    course = request.form.get("course", "").strip()
    exam_type = request.form.get("exam_type", "").strip()
    total_marks = request.form.get("total_marks", "").strip()
    difficulty = request.form.get("difficulty", "").strip()
    num_questions = request.form.get("num_questions", "20").strip()
    pattern = request.form.get("pattern", "").strip()
    syllabus = request.form.get("syllabus", "").strip()
    generation_mode = request.form.get("generation_mode", "gemini").strip().lower()
    exam_date_raw = request.form.get("exam_date", "").strip()

    if not subject_id:
        flash("Please select a subject.", "error")
        return redirect(url_for("generate_question_paper"))

    if not course:
        flash("Please select a subject with a valid course/class.", "error")
        return redirect(url_for("generate_question_paper"))

    if not exam_type:
        flash("Please select type of exam.", "error")
        return redirect(url_for("generate_question_paper"))

    if exam_type == "SEE":
        total_marks = "80"
    elif exam_type == "CIA":
        total_marks = "20"
    elif exam_type == "Other":
        if not total_marks:
            flash("Please select total marks for Other exam.", "error")
            return redirect(url_for("generate_question_paper"))
    else:
        flash("Invalid type of exam.", "error")
        return redirect(url_for("generate_question_paper"))

    try:
        total_marks_int = int(total_marks)
    except (TypeError, ValueError):
        flash("Invalid total marks.", "error")
        return redirect(url_for("generate_question_paper"))

    # ---------- DATE OF EXAMINATION ----------

    if not exam_date_raw:
        flash("Please select the date of examination.", "error")
        return redirect(url_for("generate_question_paper"))

    try:
        exam_date_obj = datetime.strptime(exam_date_raw, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date of examination.", "error")
        return redirect(url_for("generate_question_paper"))

    if exam_date_obj < datetime.now().date():
        flash("Date of examination cannot be in the past.", "error")
        return redirect(url_for("generate_question_paper"))

    # Display format used on the question paper (DD-MM-YYYY)
    exam_date_display = exam_date_obj.strftime("%d-%m-%Y")

    # ------------------------------------------

    if not difficulty:
        flash("Please select difficulty.", "error")
        return redirect(url_for("generate_question_paper"))

    if not pattern:
        flash("Please select question pattern.", "error")
        return redirect(url_for("generate_question_paper"))

    try:
        num_questions = int(num_questions)
    except (TypeError, ValueError):
        flash("Invalid number of questions.", "error")
        return redirect(url_for("generate_question_paper"))

    if num_questions < 1:
        flash("Number of questions must be at least 1.", "error")
        return redirect(url_for("generate_question_paper"))

    if num_questions > 100:
        flash("Maximum 100 questions allowed.", "error")
        return redirect(url_for("generate_question_paper"))

    
    attempt_count = None

    if pattern in PATTERN_MARKS:
        per_q = PATTERN_MARKS[pattern]
        if total_marks_int % per_q != 0:
            flash(f"{pattern} questions carry {per_q} mark(s) each, so total marks "
                  f"must be a multiple of {per_q}.", "error")
            return redirect(url_for("generate_question_paper"))
        counts = get_pattern_counts(pattern, total_marks_int)
        num_questions = counts["given"]
        attempt_count = counts["attempt"]

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("generate_question_paper"))

    cursor = connection.cursor(dictionary=True)

    assigned_subject = None

    try:
        cursor.execute(
            """
            SELECT
                s.subject_id,
                s.subject_name,
                s.course_id
            FROM subject_faculty sf

            INNER JOIN subject s
                ON sf.subject_id = s.subject_id

            WHERE
                sf.faculty_id = %s
                AND sf.subject_id = %s

            LIMIT 1
            """,
            (session["faculty_id"], subject_id)
        )

        assigned_subject = cursor.fetchone()

    except Exception as e:
        print("Subject verification error:", e)

    finally:
        cursor.close()
        connection.close()

    if not assigned_subject:
        flash("This subject is not assigned to you.", "error")
        return redirect(url_for("generate_question_paper"))

    course = assigned_subject["course_id"]

    subject_name = assigned_subject.get("subject_name") or subject_id

    if not syllabus:

        connection = get_connection()

        if connection:

            cursor = connection.cursor(dictionary=True)

            try:
                cursor.execute(
                    """
                    SELECT
                        syllabus_text
                    FROM syllabi

                    WHERE
                        faculty_id = %s
                        AND subject = %s

                    ORDER BY
                        uploaded_at DESC

                    LIMIT 1
                    """,
                    (session["faculty_id"], subject_id)
                )

                latest_syllabus = cursor.fetchone()

            except Exception as e:
                print("Syllabus loading error:", e)
                latest_syllabus = None

            finally:
                cursor.close()
                connection.close()

            if latest_syllabus:
                syllabus = latest_syllabus["syllabus_text"] or ""

    if not syllabus.strip():
        flash(
            "No syllabus found for this subject. "
            "Please upload the syllabus first.",
            "error"
        )
        return redirect(url_for("generate_question_paper"))

    preview_topics = extract_topics_from_syllabus(syllabus, subject_id)

    if not topics_are_usable(preview_topics, subject_id):
        flash(
            "Could not extract usable topics from this syllabus. "
            "Please check that it uses clear Unit/Module headings "
            "and proper punctuation, then re-upload it.",
            "error"
        )
        return redirect(url_for("generate_question_paper"))

    mixed_counts = {}
    mixed_given_counts = {}
    mixed_sections = []

    if pattern == "Mixed":

        mixed_counts = get_mixed_section_counts(total_marks_int)

        if mixed_counts is None:
            flash(
                "Mixed pattern is available for 50, 60, 70, 80 and 100 marks.",
                "error"
            )
            return redirect(url_for("generate_question_paper"))

        mixed_given_counts = get_mixed_given_counts(total_marks_int)

        mixed_sections = MIXED_SECTIONS

    if generation_mode not in ("template", "gemini"):
        generation_mode = "gemini"

    questions = None

    if generation_mode == "gemini":

        try:
            questions = create_questions_gemini(
                subject_name=subject_name,
                syllabus=syllabus,
                number=num_questions,
                pattern=pattern,
                difficulty=difficulty,
                total_marks=total_marks_int
            )

        except GeminiError as e:
            print("AI generation error:", e)
            flash(f"AI generation failed: {e}. Please try again.", "error")
            return redirect(url_for("generate_question_paper"))

        except Exception as e:
            print("AI generation error:", repr(e))
            flash("AI generation failed. Please try again.", "error")
            return redirect(url_for("generate_question_paper"))

    else:
        try:
            questions = create_questions(
                subject=subject_id,
                syllabus=syllabus,
                number=num_questions,
                pattern=pattern,
                difficulty=difficulty,
                total_marks=total_marks_int
            )
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("generate_question_paper"))

    if not questions:
        flash("Unable to generate questions.", "error")
        return redirect(url_for("generate_question_paper"))

    problems = validate_generated_questions(questions, pattern, total_marks_int)

    if problems:
        print("Question validation failed:", problems)
        flash(
            "Generated paper did not meet the pattern rules: "
            + "; ".join(problems[:3]) + ". Please try again.",
            "error"
        )
        return redirect(url_for("generate_question_paper"))

    # Mixed papers give more questions than are attempted; save the real count.
    if pattern == "Mixed":
        num_questions = sum(mixed_given_counts.values())

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("generate_question_paper"))

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO question_papers
            (
                faculty_id,
                subject,
                course,
                total_marks,
                difficulty,
                num_questions,
                pattern,
                syllabus,
                questions,
                exam_date,
                academic_year
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session["faculty_id"],
                subject_id,
                course,
                total_marks_int,
                difficulty,
                num_questions,
                pattern,
                syllabus,
                json.dumps(questions),
                exam_date_obj,
                get_current_academic_year()
            )
        )

        # Save every generated question to the question bank so
        # students can see them. Same transaction as the paper.
        save_questions_to_bank(
            cursor,
            session["faculty_id"],
            subject_id,
            difficulty,
            questions
        )

        connection.commit()

    except Exception as e:
        connection.rollback()
        print("Question paper database error:", e)
        flash("Unable to save question paper.", "error")
        return redirect(url_for("generate_question_paper"))

    finally:
        cursor.close()
        connection.close()

    if total_marks_int == 80:
        duration = "3 Hours"
    elif total_marks_int <= 50:
        duration = "2 Hours"
    elif total_marks_int >= 100:
        duration = "3 Hours"
    else:
        duration = "2 Hours 30 Minutes"

    return render_template(
        "question_paper.html",
        college_name="B.M.S College for Women",
        subject=subject_name,
        subject_id=subject_id,
        course=course,
        exam_type=exam_type,
        exam_date=exam_date_display,
        total_marks=total_marks_int,
        duration=duration,
        questions=questions,
        faculty_name=session.get("faculty_name", "Unknown"),
        created_at=datetime.now(),
        generation_mode=generation_mode,
        difficulty=difficulty,
        pattern=pattern,
        attempt_count=attempt_count,
        mixed_counts=mixed_counts,
        mixed_given_counts=mixed_given_counts,
        mixed_sections=mixed_sections
    )


# =========================================================
# VIEW SAVED QUESTION PAPER (FACULTY)
# =========================================================

@app.route("/faculty/qp-history/<int:paper_id>")
def faculty_view_question_paper(paper_id):

    if not faculty_required():
        flash("Please login as faculty.")
        return redirect(url_for("faculty_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("qp_history"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                qp.*,
                s.subject_name
            FROM question_papers qp
            LEFT JOIN subject s
                ON qp.subject = s.subject_id
            WHERE qp.id = %s
              AND qp.faculty_id = %s
            """,
            (paper_id, session["faculty_id"])
        )
        paper = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()

    if not paper:
        flash("Question paper not found.")
        return redirect(url_for("qp_history"))

    try:
        questions = json.loads(paper["questions"])
    except (TypeError, ValueError):
        questions = []

    total_marks_int = int(paper["total_marks"])

    mixed_counts = {}
    mixed_given_counts = {}
    mixed_sections = []

    if paper["pattern"] == "Mixed":
        mixed_counts = get_mixed_section_counts(total_marks_int) or {}
        mixed_given_counts = get_mixed_given_counts(total_marks_int) or {}
        mixed_sections = MIXED_SECTIONS

    if total_marks_int == 80:
        exam_type = "SEE"
    elif total_marks_int == 20:
        exam_type = "CIA"
    else:
        exam_type = "Other"

    saved_exam_date = paper.get("exam_date")

    return render_template(
        "question_paper.html",
        view_mode=True,
        college_name="B.M.S College for Women",
        subject=paper["subject_name"] or paper["subject"],
        subject_id=paper["subject"],
        course=paper["course"],
        exam_type=exam_type,
        exam_date=saved_exam_date.strftime("%d-%m-%Y") if saved_exam_date else "",
        total_marks=total_marks_int,
        duration=get_exam_duration(total_marks_int),
        questions=questions,
        faculty_name=session.get("faculty_name", "Unknown"),
        created_at=paper["created_at"],
        generation_mode="template",
        difficulty=paper["difficulty"],
        pattern=paper["pattern"],
        attempt_count=(get_pattern_counts(paper["pattern"], total_marks_int) or {}).get("attempt"),
        mixed_counts=mixed_counts,
        mixed_given_counts=mixed_given_counts,
        mixed_sections=mixed_sections
    )
# =========================================================
# STUDENT LOGIN
# =========================================================
@app.route("/student/login", methods=["GET", "POST"])
def student_login():

    if request.method == "POST":

        uucms_number = request.form.get("uucms_number", "").strip()
        password = request.form.get("password", "")

        if not uucms_number or not password:
            flash("Please enter UUCMS number and password.")
            return redirect(url_for("student_login"))

        connection = get_connection()

        if connection is None:
            flash("Database connection failed.")
            return redirect(url_for("student_login"))

        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT
                    student_id,
                    uucms_number,
                    name,
                    email,
                    password,
                    course_id,
                    section_id,
                    academic_year
                FROM student
                WHERE uucms_number = %s
                """,
                (uucms_number,)
            )

            student = cursor.fetchone()

        finally:
            cursor.close()
            connection.close()

        if not student:
            flash("Student account not found. Please check your UUCMS number.")
            return redirect(url_for("student_login"))

        try:
            password_correct = check_password_hash(student["password"], password)
        except Exception as e:
            print("Student password verification error:", e)
            password_correct = False

        if not password_correct:
            flash("Incorrect password.")
            return redirect(url_for("student_login"))

        session.clear()

        session["role"] = "student"
        session["student_logged_in"] = True
        session["student_id"] = student["student_id"]
        session["student_uucms_number"] = student["uucms_number"]
        session["student_name"] = student["name"]

        return redirect(url_for("student_dashboard"))

    return render_template("student_login.html")

# =========================================================
# STUDENT DASHBOARD
# =========================================================
@app.route("/student/dashboard")
def student_dashboard():

    if not session.get("student_logged_in"):
        flash("Please login as a student.")
        return redirect(url_for("student_login"))

    student_id = session.get("student_id")

    if not student_id:
        session.clear()
        return redirect(url_for("student_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("student_login"))

    cursor = connection.cursor(dictionary=True)

    student = None
    subjects = []
    questions = []
    papers = []

    try:

        cursor.execute(
            """
            SELECT
                student_id,
                uucms_number,
                name,
                email,
                course_id,
                section_id,
                academic_year,
                year_of_study,
                semester
            FROM student
            WHERE student_id = %s
            """,
            (student_id,)
        )

        student = cursor.fetchone()

        if not student:
            session.clear()
            flash("Student account not found.")
            return redirect(url_for("student_login"))

        year_of_study = student.get("year_of_study")
        semester = student.get("semester")
        current_year = get_current_academic_year()

        # Current subjects (this year, own section)
        cursor.execute(
            """
            SELECT DISTINCT
                s.subject_id,
                s.subject_name,
                s.course_id,
                s.semester,
                s.credits,
                s.total_marks,
                s.see_marks,
                s.cia_marks,
                s.hours_per_subject,
                sf.academic_year,
                sf.section_id,
                f.name AS faculty_name

            FROM subject s

            INNER JOIN subject_faculty sf
                ON s.subject_id = sf.subject_id

            LEFT JOIN faculty f
                ON sf.faculty_id = f.faculty_id

            WHERE sf.academic_year = %s
              AND sf.section_id = %s
              AND s.course_id = %s
              AND (%s IS NULL OR sf.student_year = %s)
              AND (%s IS NULL OR s.semester IS NULL OR s.semester = %s)

            ORDER BY s.semester, s.subject_id
            """,
            (
                student["academic_year"],
                student["section_id"],
                student["course_id"],
                year_of_study, year_of_study,
                semester, semester
            )
        )

        subjects = cursor.fetchall()

        # Question bank: previous academic years only
        # (same course and semester, current year excluded)
        cursor.execute(
            """
            SELECT
                qb.question_id,
                qb.subject_id,
                qb.question_text,
                qb.marks,
                qb.difficulty_level,
                qb.academic_year,
                s.semester

            FROM question_bank qb

            INNER JOIN subject s
                ON s.subject_id = qb.subject_id

            WHERE s.course_id = %s
              AND (%s IS NULL OR s.semester IS NULL OR s.semester = %s)
              AND qb.academic_year < %s

            ORDER BY qb.academic_year DESC, qb.subject_id, qb.question_id
            """,
            (
                student["course_id"],
                semester, semester,
                current_year
            )
        )

        questions = cursor.fetchall()

        # Question papers: previous academic years only
        cursor.execute(
            """
            SELECT
                qp.id AS paper_id,
                qp.subject,
                qp.course,
                qp.academic_year AS paper_year,
                qp.total_marks,
                qp.difficulty,
                qp.pattern,
                qp.exam_date,
                qp.created_at,
                s.subject_name,
                s.semester
            FROM question_papers qp
            INNER JOIN subject s
                ON qp.subject = s.subject_id
            WHERE s.course_id = %s
              AND (%s IS NULL OR s.semester IS NULL OR s.semester = %s)
              AND qp.academic_year < %s
            ORDER BY qp.created_at DESC
            """,
            (
                student["course_id"],
                semester, semester,
                current_year
            )
        )

        papers = cursor.fetchall()

    except Exception as e:
        print("Student dashboard error:", e)
        flash("Unable to load student dashboard.")

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "student_dashboard.html",
        student=student,
        subjects=subjects,
        questions=questions,
        papers=papers
    )

# =========================================================
# STUDENT SUBJECTS
# =========================================================
@app.route("/student/subjects")
def student_subjects():

    if not session.get("student_logged_in"):
        flash("Please login as a student.")
        return redirect(url_for("student_login"))

    student_id = session.get("student_id")

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("student_dashboard"))

    cursor = connection.cursor(dictionary=True)

    student = None
    subjects = []

    try:
        cursor.execute(
            """
            SELECT
                student_id,
                uucms_number,
                name,
                email,
                course_id,
                section_id,
                academic_year,
                year_of_study,
                semester
            FROM student
            WHERE student_id = %s
            """,
            (student_id,)
        )

        student = cursor.fetchone()

        if student:

            year_of_study = student.get("year_of_study")
            semester = student.get("semester")

            cursor.execute(
                """
                SELECT DISTINCT
                    s.subject_id,
                    s.subject_name,
                    s.course_id,
                    s.semester,
                    s.credits,
                    s.total_marks,
                    s.see_marks,
                    s.cia_marks,
                    s.hours_per_subject,
                    f.name AS faculty_name

                FROM subject s

                INNER JOIN subject_faculty sf
                    ON s.subject_id = sf.subject_id

                LEFT JOIN faculty f
                    ON sf.faculty_id = f.faculty_id

                WHERE sf.academic_year = %s
                  AND sf.section_id = %s
                  AND s.course_id = %s
                  AND (%s IS NULL OR sf.student_year = %s)
                  AND (%s IS NULL OR s.semester IS NULL OR s.semester = %s)

                ORDER BY s.semester, s.subject_id
                """,
                (
                    student["academic_year"],
                    student["section_id"],
                    student["course_id"],
                    year_of_study, year_of_study,
                    semester, semester
                )
            )

            subjects = cursor.fetchall()

    finally:
        cursor.close()
        connection.close()

    if not student:
        session.clear()
        flash("Student account not found.")
        return redirect(url_for("student_login"))

    return render_template(
        "student_subjects.html",
        student=student,
        subjects=subjects
    )


# =========================================================
# STUDENT QUESTION BANK
# =========================================================
@app.route("/student/question-bank")
def student_question_bank():

    if not session.get("student_logged_in"):
        flash("Please login as a student.")
        return redirect(url_for("student_login"))

    student_id = session.get("student_id")

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("student_dashboard"))

    cursor = connection.cursor(dictionary=True)

    student = None
    questions = []

    try:

        cursor.execute(
            """
            SELECT
                student_id,
                uucms_number,
                name,
                email,
                course_id,
                section_id,
                academic_year,
                year_of_study,
                semester
            FROM student
            WHERE student_id = %s
            """,
            (student_id,)
        )

        student = cursor.fetchone()

        if student:

            semester = student.get("semester")
            current_year = get_current_academic_year()

            # Previous academic years only (same course and semester,
            # current year excluded)
            cursor.execute(
                """
                SELECT
                    qb.question_id,
                    qb.subject_id,
                    qb.question_text,
                    qb.marks,
                    qb.difficulty_level,
                    qb.academic_year,
                    s.semester

                FROM question_bank qb

                INNER JOIN subject s
                    ON s.subject_id = qb.subject_id

                WHERE s.course_id = %s
                  AND (%s IS NULL OR s.semester IS NULL OR s.semester = %s)
                  AND qb.academic_year < %s

                ORDER BY qb.academic_year DESC, qb.subject_id, qb.question_id
                """,
                (
                    student["course_id"],
                    semester, semester,
                    current_year
                )
            )

            questions = cursor.fetchall()

    finally:
        cursor.close()
        connection.close()

    if not student:
        session.clear()
        flash("Student account not found.")
        return redirect(url_for("student_login"))

    return render_template(
        "student_question_bank.html",
        student=student,
        questions=questions
    )
# =========================================================
# STUDENT PROFILE
# =========================================================
@app.route("/student/profile")
def student_profile():

    if not session.get("student_logged_in"):
        flash("Please login as a student.")
        return redirect(url_for("student_login"))

    student_id = session.get("student_id")

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("student_dashboard"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                student_id,
                uucms_number,
                name,
                email,
                course_id,
                section_id,
                academic_year
            FROM student
            WHERE student_id = %s
            """,
            (student_id,)
        )

        student = cursor.fetchone()

    finally:
        cursor.close()
        connection.close()

    if not student:
        session.clear()
        flash("Student account not found.")
        return redirect(url_for("student_login"))

    return render_template("student_profile.html", student=student)



# =========================================================
# STUDENT QUESTION PAPERS (LIST)
# =========================================================
@app.route("/student/question-papers")
def student_question_papers():

    if not session.get("student_logged_in"):
        flash("Please login as a student.")
        return redirect(url_for("student_login"))

    student_id = session.get("student_id")

    if not student_id:
        session.clear()
        return redirect(url_for("student_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("student_dashboard"))

    cursor = connection.cursor(dictionary=True)

    student = None
    papers = []

    try:
        cursor.execute(
            """
            SELECT
                student_id, uucms_number, name, course_id,
                section_id, academic_year, year_of_study, semester
            FROM student
            WHERE student_id = %s
            """,
            (student_id,)
        )
        student = cursor.fetchone()

        if not student:
            session.clear()
            flash("Student account not found.")
            return redirect(url_for("student_login"))

        semester = student.get("semester")

        cursor.execute(
            """
            SELECT
                qp.id AS paper_id,
                qp.subject,
                qp.course,
                qp.academic_year AS paper_year,
                qp.total_marks,
                qp.difficulty,
                qp.pattern,
                qp.exam_date,
                qp.created_at,
                s.subject_name,
                s.semester
            FROM question_papers qp
            INNER JOIN subject s
                ON qp.subject = s.subject_id
            WHERE s.course_id = %s
              AND (%s IS NULL OR s.semester IS NULL OR s.semester = %s)
              AND qp.academic_year < %s
            ORDER BY qp.created_at DESC
            """,
            (
                student["course_id"],
                semester, semester,
                get_current_academic_year()
            )
        )
        papers = cursor.fetchall()

    except Exception as e:
        print("Student question papers list error:", e)
        flash("Unable to load question papers.")

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "student_question_papers.html",
        student=student,
        papers=papers
    )
# =========================================================
# STUDENT VIEW / PRINT SINGLE QUESTION PAPER
# =========================================================
@app.route("/student/question-paper/<int:paper_id>")
def student_view_question_papers(paper_id):

    if not session.get("student_logged_in"):
        flash("Please login as a student.")
        return redirect(url_for("student_login"))

    student_id = session.get("student_id")

    if not student_id:
        session.clear()
        return redirect(url_for("student_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("student_question_papers"))

    cursor = connection.cursor(dictionary=True)

    student = None
    paper = None
    questions = []

    try:

        cursor.execute(
            """
            SELECT
                student_id,
                uucms_number,
                name,
                course_id,
                section_id,
                academic_year,
                year_of_study,
                semester
            FROM student
            WHERE student_id = %s
            """,
            (student_id,)
        )

        student = cursor.fetchone()

        if not student:
            session.clear()
            flash("Student account not found.")
            return redirect(url_for("student_login"))

        semester = student.get("semester")
        current_year = get_current_academic_year()

        # Same rules as the list page: own course/semester, previous years only
        cursor.execute(
            """
            SELECT
                qp.id,
                qp.faculty_id,
                qp.subject,
                qp.course,
                qp.academic_year AS paper_year,
                qp.total_marks,
                qp.difficulty,
                qp.num_questions,
                qp.pattern,
                qp.exam_date,
                qp.syllabus,
                qp.questions,
                qp.created_at,
                s.subject_name,
                s.semester
            FROM question_papers qp
            INNER JOIN subject s
                ON qp.subject = s.subject_id
            WHERE qp.id = %s
              AND s.course_id = %s
              AND (%s IS NULL OR s.semester IS NULL OR s.semester = %s)
              AND qp.academic_year < %s
            """,
            (paper_id, student["course_id"], semester, semester, current_year)
        )

        paper = cursor.fetchone()

        if not paper:
            # Not found, other course/semester, or current-year paper
            abort(404)

        # Parse stored questions (JSON text); fall back to plain text
        raw_questions = paper.get("questions")
        if raw_questions:
            try:
                questions = json.loads(raw_questions)
            except (ValueError, TypeError):
                questions = [{"question_text": raw_questions}]

        paper["paper_id"] = paper["id"]
        paper["paper_code"] = f"QP-{paper['id']:04d}"
        paper["duration"] = get_exam_duration(paper["total_marks"])

        paper_total_marks = int(paper["total_marks"])
        mixed_counts = {}
        mixed_given_counts = {}

        if paper["pattern"] == "Mixed":
            mixed_counts = get_mixed_section_counts(paper_total_marks) or {}
            mixed_given_counts = get_mixed_given_counts(paper_total_marks) or {}

        attempt_count = (get_pattern_counts(paper["pattern"], paper_total_marks) or {}).get("attempt")

        sem_value = paper.get("semester") or student.get("semester")
        sem_label = f"Sem {sem_value}" if sem_value else ""
        paper["year_semester"] = " · ".join(
            part for part in (
                paper.get("course") or student["course_id"] or "",
                sem_label,
                paper.get("paper_year") or ""
            ) if part
        )

    except Exception as e:
        # Let abort(404) pass through instead of swallowing it
        if hasattr(e, "code") and e.code == 404:
            raise
        print("Student question paper error:", e)
        flash("Unable to load the question paper.")
        return redirect(url_for("student_question_papers"))

    finally:
        cursor.close()
        connection.close()

    is_print = request.args.get("print") == "1"

    return render_template(
        "student_view_question_papers.html",
        student=student,
        paper=paper,
        questions=questions,
        mixed_counts=mixed_counts,
        mixed_given_counts=mixed_given_counts,
        attempt_count=attempt_count,
        is_print=is_print
    )


@app.route("/student/update-semester", methods=["POST"])
def student_update_semester():

    if not session.get("student_logged_in"):
        flash("Please login as a student.")
        return redirect(url_for("student_login"))

    semester = request.form.get("semester", "").strip()

    if semester not in ("1", "2", "3", "4", "5", "6", "7", "8"):
        flash("Please select a valid semester.")
        return redirect(url_for("student_profile"))

    sem = int(semester)
    year_of_study = (sem + 1) // 2

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("student_profile"))

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            UPDATE student
            SET semester = %s,
                year_of_study = %s
            WHERE student_id = %s
            """,
            (sem, year_of_study, session["student_id"])
        )
        connection.commit()
        flash("Semester updated.")

    except Exception as e:
        connection.rollback()
        print("Update semester error:", e)
        flash("Unable to update semester.")

    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("student_profile"))

# =========================================================
# STUDENT CHANGE PASSWORD
# =========================================================
@app.route("/student/change-password", methods=["GET", "POST"])
def student_change_password():

    if not session.get("student_logged_in"):
        flash("Please login as a student.")
        return redirect(url_for("student_login"))

    student_id = session.get("student_id")

    if not student_id:
        session.clear()
        return redirect(url_for("student_login"))

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.")
        return redirect(url_for("student_dashboard"))

    cursor = connection.cursor(dictionary=True)

    student = None

    try:

        cursor.execute(
            """
            SELECT
                student_id,
                uucms_number,
                name,
                email,
                course_id,
                section_id,
                academic_year,
                password
            FROM student
            WHERE student_id = %s
            """,
            (student_id,)
        )

        student = cursor.fetchone()

        if not student:
            session.clear()
            flash("Student account not found.")
            return redirect(url_for("student_login"))

        if request.method == "POST":

            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if (
                not current_password
                or not new_password
                or not confirm_password
            ):
                flash("Please fill in all password fields.")
                return redirect(url_for("student_change_password"))

            try:
                password_correct = check_password_hash(student["password"], current_password)
            except Exception as e:
                print("Student password verification error:", e)
                password_correct = False

            if not password_correct:
                flash("Current password is incorrect.")
                return redirect(url_for("student_change_password"))

            if len(new_password) < 8:
                flash("New password must contain at least 8 characters.")
                return redirect(url_for("student_change_password"))

            if new_password != confirm_password:
                flash("New password and confirm password do not match.")
                return redirect(url_for("student_change_password"))

            if check_password_hash(student["password"], new_password):
                flash("New password must be different from your current password.")
                return redirect(url_for("student_change_password"))

            new_password_hash = generate_password_hash(new_password)

            cursor.execute(
                """
                UPDATE student
                SET password = %s
                WHERE student_id = %s
                """,
                (new_password_hash, student_id)
            )

            connection.commit()

            flash("Password changed successfully.")

            return redirect(url_for("student_profile"))

    except Exception as e:
        connection.rollback()
        print("Student change password error:", e)
        flash("Unable to change password.")

    finally:
        cursor.close()
        connection.close()

    return render_template("student_change_password.html", student=student)


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():
    session.clear()
    return render_template("logout.html")


# =========================================================
# 404 ERROR
# =========================================================

@app.errorhandler(404)
def page_not_found(error):
    return """
    <h1>404 - Page Not Found</h1>
    <p>The requested page does not exist.</p>
    <a href="/">Back to Home</a>
    """, 404


# =========================================================
# 500 ERROR
# =========================================================

@app.errorhandler(500)
def internal_error(error):
    return """
    <h1>500 - Internal Server Error</h1>
    <p>An internal server error occurred.</p>
    <a href="/">Back to Home</a>
    """, 500


# =========================================================
# INITIALIZE DATABASE
# =========================================================

try:
    initialize_database()
except Exception as e:
    print("Database initialization error:", e)

ensure_otp_schema()
ensure_student_schema()
ensure_subject_schema()
ensure_semester_schema()
ensure_question_paper_schema()

# =========================================================
# START APPLICATION
# =========================================================

if __name__ == "__main__":
    # Debug mode is OFF unless you set FLASK_DEBUG=1 in your .env
    app.run(
        debug=os.environ.get("FLASK_DEBUG") == "1",
        host="127.0.0.1",
        port=5000
    )