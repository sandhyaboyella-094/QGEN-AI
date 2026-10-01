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
    jsonify
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

import requests
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from werkzeug.utils import secure_filename

from pypdf import PdfReader

# OCR support for scanned/image-based syllabus PDFs
try:
    import pymupdf # PyMuPDF - renders PDF pages to images
    import pytesseract
    from PIL import Image
except ImportError:
    pymupdf = None
    pytesseract = None
    Image = None

# If Tesseract is installed in the standard Windows location, use it
# automatically even when it has not been added to PATH.
if pytesseract is not None and os.name == "nt":
    _tesseract_default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(_tesseract_default):
        pytesseract.pytesseract.tesseract_cmd = _tesseract_default

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
    # Short, direct 2-mark questions. These templates are deliberately
    # noun-phrase safe so topics such as "AES structure" or
    # "Public-Key Cryptography principles" do not produce bad grammar.
    "remembering": [
        "Define {topic}.",
        "State the main purpose of {topic}.",
        "List any two important features of {topic}.",
        "State any two key principles of {topic}.",
        "Mention any two applications of {topic}.",
        "What is meant by {topic}?"
    ],

    "understanding": [
        "Explain the basic concept of {topic}.",
        "Describe the main idea of {topic}.",
        "Explain the working of {topic} in brief.",
        "Describe the main characteristics of {topic}.",
        "Explain the importance of {topic} in network security.",
        "Illustrate {topic} with a simple example."
    ],

    "applying": [
        "Using a suitable example, demonstrate how {topic} works.",
        "Apply the principles of {topic} to a simple practical situation.",
        "Demonstrate the use of {topic} with a suitable example.",
        "Explain how {topic} can be applied to solve a practical problem.",
        "Show the steps involved in applying {topic} to a suitable example.",
        "Illustrate the application of {topic} in a real-world situation."
    ],

    "analyzing": [
        "Explain the main components of {topic} and their functions.",
        "Analyze the working of {topic} and explain its important stages.",
        "Compare {topic} with a closely related technique and state their differences.",
        "Analyze the advantages and limitations of {topic}.",
        "Examine the factors that affect the effectiveness of {topic}.",
        "Analyze the possible problems that can occur when using {topic}."
    ],

    "evaluating": [
        "Evaluate the effectiveness of {topic} in a suitable application.",
        "Discuss the advantages and limitations of {topic} with suitable justification.",
        "Justify the use of {topic} for a suitable security requirement.",
        "Assess the suitability of {topic} for a given practical situation.",
        "Evaluate the security significance of {topic}.",
        "Critically examine the strengths and limitations of {topic}."
    ],

    "creating": [
        "Design a suitable solution using the principles of {topic}.",
        "Propose a suitable approach based on {topic} for a practical security problem.",
        "Construct a simple example that demonstrates the key concept of {topic}.",
        "Develop a suitable procedure for applying {topic} to a security problem.",
        "Design a small system component based on the principles of {topic}.",
        "Propose an improved approach for applying {topic} in a practical situation."
    ]
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
# PDF TEXT + OCR EXTRACTION
# =========================================================

def extract_syllabus_text(pdf_path):
    """
    Extract syllabus text from both normal text PDFs and scanned/image PDFs.

    Normal PDF text is used first. If a page contains little/no extractable
    text, that page is rendered as an image and OCR is applied to it.
    """

    reader = PdfReader(pdf_path)
    page_texts = []
    pages_needing_ocr = []

    # First pass: normal PDF text extraction.
    for page_index, page in enumerate(reader.pages):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as e:
            print(f"PDF text extraction error on page {page_index + 1}: {e}")
            text = ""

        page_texts.append(text)

        # A scanned page normally returns an empty/very small text layer.
        compact_text = re.sub(r"\s+", "", text)
        if len(compact_text) < 25:
            pages_needing_ocr.append(page_index)

    # If all pages already contain useful text, no OCR is necessary.
    if not pages_needing_ocr:
        return "\n\n".join(
            text for text in page_texts if text.strip()
        ).strip()

    # OCR fallback for scanned/image-based pages.
    if pymupdf is None or pytesseract is None or Image is None:
        raise RuntimeError(
            "OCR packages are not installed. Install PyMuPDF, pytesseract, "
            "and Pillow, and install the Tesseract OCR application."
        )

    try:
        pdf_document = pymupdf.open(pdf_path)
    except Exception as e:
        raise RuntimeError(f"Unable to open PDF for OCR: {e}") from e

    try:
        for page_index in pages_needing_ocr:
            try:
                pdf_page = pdf_document.load_page(page_index)

                # 2x rendering gives OCR a clearer image while keeping memory
                # usage reasonable for normal syllabus pages.
                matrix = pymupdf.Matrix(2.0, 2.0)
                pix = pdf_page.get_pixmap(matrix=matrix, alpha=False)

                image = Image.frombytes(
                    "RGB",
                    [pix.width, pix.height],
                    pix.samples
                )

                ocr_text = pytesseract.image_to_string(
                    image,
                    lang="eng",
                    config="--psm 6"
                ).strip()

                if ocr_text:
                    page_texts[page_index] = ocr_text

            except Exception as e:
                print(
                    f"OCR error on page {page_index + 1}: {e}"
                )

    finally:
        pdf_document.close()

    return "\n\n".join(
        text for text in page_texts if text.strip()
    ).strip()


# =========================================================
# LOGIN CHECKS
# =========================================================

def admin_required():
    return session.get("role") == "admin"


def faculty_required():
    return session.get("role") == "faculty"


def hod_required():
    return session.get("role") == "hod"


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
        f"Your QGEN-AI verification code is {otp}.\n\n"
        f"It is valid for {OTP_TTL_MIN} minutes. "
        "If you did not request it, you can ignore this email."
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
            """
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
            ORDER BY course_id, subject_id
            """
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
                see_duration
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
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
                see_duration
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
                    subject_id
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
            """,
            (subject_id,)
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
            """,
            (subject_id,)
        )

        subject = cursor.fetchone()

        if not subject:
            flash("Subject not found.", "error")
            return redirect(url_for("hod_subjects"))

        cursor.execute(
            """
            DELETE FROM subject
            WHERE subject_id = %s
            """,
            (subject_id,)
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


#============================================================
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
            student_year = request.form.get("student_year", "").strip()
            section_id = request.form.get("section_id", "").strip()

            if not subject_id or not faculty_id:
                flash("Subject and Faculty are required.", "error")
                return redirect(url_for("hod_assign_subject"))

            if not academic_year or not section_id:
                flash("Academic Year and Section are required.", "error")
                return redirect(url_for("hod_assign_subject"))

            if student_year not in {"1", "2", "3"}:
                flash("Please select a valid student year.", "error")
                return redirect(url_for("hod_assign_subject"))

            try:
                faculty_id = int(faculty_id)
            except ValueError:
                flash("Invalid Faculty ID.", "error")
                return redirect(url_for("hod_assign_subject"))

            # ------------------------------------------------
            # CHECK FACULTY
            # ------------------------------------------------

            if hod_department:
                cursor.execute(
                    """
                    SELECT
                        faculty_id,
                        name,
                        department
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
                    SELECT
                        faculty_id,
                        name,
                        department
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

            # ------------------------------------------------
            # CHECK SUBJECT
            # ------------------------------------------------

            cursor.execute(
                """
                SELECT
                    subject_id,
                    subject_name,
                    course_id
                FROM subject
                WHERE subject_id = %s
                """,
                (subject_id,)
            )

            subject = cursor.fetchone()

            if not subject:
                flash("Subject not found.", "error")
                return redirect(url_for("hod_assign_subject"))

            # ------------------------------------------------
            # CHECK EXISTING ASSIGNMENT
            # ------------------------------------------------

            cursor.execute(
                """
                SELECT
                    subject_id,
                    faculty_id,
                    academic_year,
                    student_year,
                    section_id
                FROM subject_faculty
                WHERE subject_id = %s
                AND faculty_id = %s
                AND academic_year = %s
                AND student_year = %s
                AND section_id = %s
                """,
                (
                    subject_id,
                    faculty_id,
                    academic_year,
                    student_year,
                    section_id
                )
            )

            existing = cursor.fetchone()

            if existing:
                flash(
                    "This subject is already assigned to this faculty "
                    "for the selected academic year, student year and section.",
                    "error"
                )
                return redirect(url_for("hod_assign_subject"))

            # ------------------------------------------------
            # INSERT ASSIGNMENT
            # ------------------------------------------------

            cursor.execute(
                """
                INSERT INTO subject_faculty
                (
                    subject_id,
                    faculty_id,
                    academic_year,
                    student_year,
                    section_id
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
                    subject_id,
                    faculty_id,
                    academic_year,
                    student_year,
                    section_id
                )
            )

            connection.commit()

            flash("Subject assigned successfully.", "success")

            return redirect(url_for("hod_assign_subject"))

        # ----------------------------------------------------
        # GET SUBJECTS
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                subject_id,
                subject_name,
                course_id
            FROM subject
            ORDER BY course_id, subject_id
            """
        )

        subjects = cursor.fetchall()

        # ----------------------------------------------------
        # GET FACULTY
        # ----------------------------------------------------

        if hod_department:
            cursor.execute(
                """
                SELECT
                    faculty_id,
                    name,
                    email,
                    department
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
                SELECT
                    faculty_id,
                    name,
                    email,
                    department
                FROM faculty
                WHERE role = 'faculty'
                ORDER BY faculty_id ASC
                """
            )

        faculty = cursor.fetchall()

        # ----------------------------------------------------
        # GET ASSIGNMENTS
        # ----------------------------------------------------

        if hod_department:
            cursor.execute(
                """
                SELECT
                    sf.subject_id,
                    s.subject_name,
                    sf.faculty_id,
                    sf.academic_year,
                    sf.student_year,
                    sf.section_id,
                    f.name AS faculty_name,
                    s.course_id
                FROM subject_faculty sf

                LEFT JOIN faculty f
                    ON sf.faculty_id = f.faculty_id

                LEFT JOIN subject s
                    ON sf.subject_id = s.subject_id

                WHERE f.department = %s

                ORDER BY
                    sf.academic_year DESC,
                    sf.student_year,
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
                    sf.student_year,
                    sf.section_id,
                    f.name AS faculty_name,
                    s.course_id
                FROM subject_faculty sf

                LEFT JOIN faculty f
                    ON sf.faculty_id = f.faculty_id

                LEFT JOIN subject s
                    ON sf.subject_id = s.subject_id

                ORDER BY
                    sf.academic_year DESC,
                    sf.student_year,
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

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("hod_assign_subject"))

    cursor = connection.cursor(dictionary=True)

    try:

        hod_department = session.get("department")

        if hod_department:
            cursor.execute(
                """
                SELECT
                    sf.subject_id,
                    sf.faculty_id,
                    sf.academic_year,
                    sf.section_id
                FROM subject_faculty sf

                INNER JOIN faculty f
                    ON sf.faculty_id = f.faculty_id

                WHERE sf.subject_id = %s
                AND sf.faculty_id = %s
                AND sf.academic_year = %s
                AND sf.section_id = %s
                AND f.department = %s
                """,
                (subject_id, faculty_id, academic_year, section_id, hod_department)
            )
        else:
            cursor.execute(
                """
                SELECT
                    subject_id,
                    faculty_id,
                    academic_year,
                    section_id
                FROM subject_faculty

                WHERE subject_id = %s
                AND faculty_id = %s
                AND academic_year = %s
                AND section_id = %s
                """,
                (subject_id, faculty_id, academic_year, section_id)
            )

        assignment = cursor.fetchone()

        if not assignment:
            flash("Subject assignment not found.", "error")
            return redirect(url_for("hod_assign_subject"))

        cursor.execute(
            """
            DELETE FROM subject_faculty

            WHERE subject_id = %s
            AND faculty_id = %s
            AND academic_year = %s
            AND section_id = %s
            """,
            (subject_id, faculty_id, academic_year, section_id)
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
            FROM syllabi
            WHERE id = %s
            """,
            (syllabus_id,)
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
            FROM syllabi
            WHERE id = %s
            """,
            (syllabus_id,)
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
                qp.syllabus,
                qp.questions,
                qp.created_at,
                f.name AS faculty_name
            FROM question_papers qp
            LEFT JOIN faculty f
                ON qp.faculty_id = f.faculty_id
            WHERE qp.id = %s
            """,
            (paper_id,)
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

    questions = paper.get("questions")

    return render_template(
        "hod_view_question_paper.html",
        paper=paper,
        questions=questions,
        college_name="B.M.S College for Women",
        exam_title="END – SEMESTER EXAMINATION",
        academic_session=session.get("academic_session", ""),
        duration="2 Hours 30 Minutes"
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
                id,
                questions
            FROM question_papers
            WHERE id = %s
            """,
            (paper_id,)
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
def admin_view_question_paper(paper_id):

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

    total_marks_int = paper["total_marks"]

    if total_marks_int <= 50:
        duration = "2 Hours"
    elif total_marks_int >= 100:
        duration = "3 Hours"
    else:
        duration = "2 Hours 30 Minutes"

    return render_template(
        "admin_view_question_paper.html",
        college_name="B.M.S College for Women",
        subject=paper["subject"],
        course=paper["course"],
        total_marks=total_marks_int,
        duration=duration,
        questions=questions,
        faculty_name=paper["faculty_name"],
        created_at=paper["created_at"]
    )


# =========================================================
# DELETE QUESTION PAPER (ADMIN)
# =========================================================

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
            course_id = request.form.get("course_id", "").strip()
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
                    DATE_FORMAT(created_at, '%%Y-%%m') AS month_key,
                    DATE_FORMAT(created_at, '%%b') AS month_label,
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
                    '%%Y-%%m'
                ) AS month_key,

                COUNT(*) AS total

            FROM question_papers

            WHERE faculty_id = %s

              AND created_at >= %s

              AND created_at < %s

            GROUP BY
                DATE_FORMAT(
                    created_at,
                    '%%Y-%%m'
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
                    '%%Y-%%m'
                ) AS month_key,

                COUNT(*) AS total

            FROM syllabi

            WHERE faculty_id = %s

              AND uploaded_at >= %s

              AND uploaded_at < %s

            GROUP BY
                DATE_FORMAT(
                    uploaded_at,
                    '%%Y-%%m'
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
                # Supports both text-based PDFs and scanned/image-based PDFs.
                syllabus_text = extract_syllabus_text(filepath)

            except RuntimeError as e:
                print("Syllabus OCR setup error:", e)

                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                except Exception:
                    pass

                flash(
                    "This syllabus appears to be image-based, but OCR is not "
                    "installed. Please install PyMuPDF, pytesseract, Pillow, "
                    "and Tesseract OCR, then upload the PDF again.",
                    "error"
                )

                return redirect(url_for("upload_syllabus"))

            except Exception as e:
                print("Syllabus extraction error:", e)
                syllabus_text = ""

            if not syllabus_text.strip():

                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                except Exception:
                    pass

                flash(
                    "Unable to extract readable text from this PDF. "
                    "Please upload a clearer syllabus PDF or scanned PDF.",
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
# ============================================================
# DELETE SYLLABUS
# ============================================================

@app.route("/faculty/syllabus/delete/<int:syllabus_id>", methods=["POST"])
def delete_faculty_syllabus(syllabus_id):

    if not faculty_required():
        flash("Please login as faculty.", "error")
        return redirect(url_for("faculty_login"))

    faculty_id = session.get("faculty_id")

    connection = get_connection()

    if connection is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("syllabus_view", syllabus_id=syllabus_id))

    cursor = connection.cursor(dictionary=True)

    try:

        # ----------------------------------------------------
        # GET SYLLABUS
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                id,
                filename
            FROM syllabi
            WHERE id = %s
            AND faculty_id = %s
            """,
            (syllabus_id, faculty_id)
        )

        syllabus = cursor.fetchone()

        if not syllabus:
            flash("Syllabus not found.", "error")
            return redirect(url_for("faculty_dashboard"))

        # ----------------------------------------------------
        # DELETE DATABASE RECORD
        # ----------------------------------------------------

        cursor.execute(
            """
            DELETE FROM syllabi
            WHERE id = %s
            AND faculty_id = %s
            """,
            (syllabus_id, faculty_id)
        )

        connection.commit()

        # ----------------------------------------------------
        # DELETE PDF FILE
        # ----------------------------------------------------

        filename = syllabus.get("filename")

        if filename:

            file_path = os.path.join(
                app.config["UPLOAD_FOLDER"],
                filename
            )

            if os.path.exists(file_path):
                os.remove(file_path)

        flash("Syllabus deleted successfully.", "success")

    except Exception as e:

        connection.rollback()

        print("Delete syllabus error:", e)

        flash(
            "Unable to delete syllabus.",
            "error"
        )

    finally:

        cursor.close()
        connection.close()

    return redirect(url_for("faculty_dashboard"))

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
            r"[,;]|(?<=[A-Za-z0-9\)])\.\s+(?=[A-Z])|(?<=[A-Za-z0-9\)])[?!]\s+(?=[A-Z])",
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
        "bloom_levels": ["Analyze", "Evaluate", "Create"]
    }
]


def get_mixed_pattern(total_marks):

    total_marks = int(total_marks)

    if total_marks < 50 or total_marks % 10 != 0:
        return None

    part_c_attempt = (total_marks - 40) // 10

    if part_c_attempt < 1:
        return None

    return {
        "Part A": {"attempt": 10, "given": 12},
        "Part B": {"attempt": 4, "given": 5},
        "Part C": {"attempt": part_c_attempt, "given": part_c_attempt + 1}
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
    try:
        return template.format(topic=topic)
    except (KeyError, IndexError, ValueError):
        try:
            return template.format(topic)
        except Exception:
            return f"Explain {topic}."


def create_questions(subject, syllabus, number, pattern, difficulty, total_marks):

    topic_picker = build_topic_picker(syllabus, subject)
    mcq_topic_picker = build_topic_picker(syllabus, subject, max_words_per_topic=8)
    deep_topic_picker = build_topic_picker(syllabus, subject, min_words_per_topic=2)

    questions = []

    used_questions = set()

    used_topics = set()

    if difficulty.lower() == "easy":
        short_templates = BLOOM_TEMPLATES["Remember"]
        long_templates = BLOOM_TEMPLATES["Understand"]
    else:
        short_templates = BLOOM_TEMPLATES["Understand"]
        long_templates = BLOOM_TEMPLATES["Analyze"]

    if pattern == "Mixed":

        mixed_attempt_counts = get_mixed_section_counts(total_marks)
        mixed_given_counts = get_mixed_given_counts(total_marks)

        if not mixed_attempt_counts or not mixed_given_counts:
            raise ValueError("Mixed pattern is supported for 80 and 100 marks only.")

        bloom_template_pickers = {
            bloom_level: TemplatePicker(templates)
            for bloom_level, templates in BLOOM_TEMPLATES.items()
        }

        for section_definition in MIXED_SECTIONS:

            section_name = section_definition["name"]
            section_marks = section_definition["marks_per_question"]
            section_count = mixed_given_counts[section_name]
            bloom_levels = section_definition["bloom_levels"]

            section_topic_picker = (
                topic_picker if section_name == "Part A" else deep_topic_picker
            )

            section_unit_sequence = allocate_evenly(section_topic_picker.units(), section_count)

            if section_name == "Part C":

                if len(bloom_levels) >= 2:
                    bloom_pair_options = list(itertools.combinations(bloom_levels, 2))
                else:
                    bloom_pair_options = [(bloom_levels[0], bloom_levels[0])]

                section_bloom_pair_sequence = allocate_evenly(bloom_pair_options, section_count)

            else:
                section_bloom_sequence = allocate_evenly(bloom_levels, section_count)

            section_attempts = 0
            max_section_attempts = max(section_count * 40, 100)

            added_in_section = 0

            while added_in_section < section_count and section_attempts < max_section_attempts:

                section_attempts += 1

                unit_index = section_unit_sequence[added_in_section]

                if section_name in ["Part A", "Part B"]:

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

                    subquestions = []

                    sub_bloom_levels = list(section_bloom_pair_sequence[added_in_section])
                    random.shuffle(sub_bloom_levels)

                    sub_unit_indices = [
                        unit_index,
                        (unit_index + 1) % max(len(section_topic_picker.units()), 1)
                    ]

                    for sub_index in range(2):

                        sub_bloom = sub_bloom_levels[sub_index]

                        sub_topic = pick_unique_topic(
                            section_topic_picker,
                            sub_unit_indices[sub_index],
                            used_topics
                        )

                        sub_template = bloom_template_pickers[sub_bloom].next_template()

                        sub_text = format_bloom_question(sub_template, sub_topic)

                        sub_key = sub_text.strip().lower()

                        if sub_key in used_questions:
                            retry = 0
                            while sub_key in used_questions and retry < 10:
                                retry += 1
                                sub_template = bloom_template_pickers[sub_bloom].next_template()
                                sub_topic = pick_unique_topic(
                                    section_topic_picker,
                                    sub_unit_indices[sub_index],
                                    used_topics
                                )
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
                            "unit": f"Unit {sub_unit_indices[sub_index] + 1}",
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
                        "topics": [
                            item for sub in subquestions for item in sub.get("topics", [])
                        ]
                    })

                added_in_section += 1

            if added_in_section != section_count:
                raise ValueError(
                    f"Could not generate the required {section_count} "
                    f"questions for {section_name}."
                )

        calculated_total = sum(
            mixed_attempt_counts[section["name"]] * section["marks_per_question"]
            for section in MIXED_SECTIONS
        )

        if calculated_total != int(total_marks):
            raise ValueError(
                f"Mixed pattern total is {calculated_total}, "
                f"but {total_marks} marks were requested."
            )

        return questions

    marks_distribution = compute_marks_distribution(total_marks, number)

    mcq_template_picker = TemplatePicker(mcq_templates)
    short_template_picker = TemplatePicker(short_templates)
    medium_template_picker = TemplatePicker(medium_pattern_templates)
    long_template_picker = TemplatePicker(long_templates)

    if pattern == "MCQ":
        active_topic_picker = mcq_topic_picker
    elif pattern in ("Medium", "Long"):
        active_topic_picker = deep_topic_picker
    else:
        active_topic_picker = topic_picker

    unit_sequence = allocate_evenly(active_topic_picker.units(), number)

    attempts = 0
    max_attempts = max(number * 30, 100)

    while len(questions) < number and attempts < max_attempts:

        attempts += 1

        current_marks = marks_distribution[len(questions)]
        current_unit_index = unit_sequence[len(questions)]
        current_unit_label = f"Unit {current_unit_index + 1}"

        if pattern == "MCQ":

            topic = pick_unique_topic(mcq_topic_picker, current_unit_index, used_topics)
            template = mcq_template_picker.next_template()

            question_text = format_bloom_question(template, topic)
            question_key = question_text.lower()

            if question_key in used_questions:
                continue

            used_questions.add(question_key)

            questions.append({
                "text": question_text,
                "options": [
                    f"Related to {topic}",
                    "An unrelated concept",
                    "None of these",
                    "All of these"
                ],
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
    generation_mode = request.form.get("generation_mode", "template").strip()

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
                "For Mixed pattern, Part A questions are "
                "2 marks, Part B questions are 5 marks, "
                "and Part C questions are 10 marks. "
                "Choose a compatible total marks.",
                "error"
            )
            return redirect(url_for("generate_question_paper"))

        mixed_given_counts = get_mixed_given_counts(total_marks_int)

        mixed_sections = MIXED_SECTIONS

    questions = create_questions(
        subject=subject_id,
        syllabus=syllabus,
        number=num_questions,
        pattern=pattern,
        difficulty=difficulty,
        total_marks=total_marks_int
    )

    if not questions:
        flash("Unable to generate questions.", "error")
        return redirect(url_for("generate_question_paper"))

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
                questions
            )

            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
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
                json.dumps(questions)
            )
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
        total_marks=total_marks_int,
        duration=duration,
        questions=questions,
        faculty_name=session.get("faculty_name", "Unknown"),
        created_at=datetime.now(),
        generation_mode=generation_mode,
        difficulty=difficulty,
        pattern=pattern,
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

        if not student:
            session.clear()
            flash("Student account not found.")
            return redirect(url_for("student_login"))

        cursor.execute(
            """
            SELECT DISTINCT
                s.subject_id,
                s.subject_name,
                s.course_id,
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

            ORDER BY s.subject_id
            """,
            (
                student["academic_year"],
                student["section_id"],
                student["course_id"]
            )
        )

        subjects = cursor.fetchall()

        # -----------------------------------------------------
        # GET QUESTION BANK (fixed: real column names)
        # -----------------------------------------------------

        cursor.execute(
            """
            SELECT DISTINCT
                qb.question_id,
                qb.subject_id,
                qb.question_text,
                qb.marks,
                qb.difficulty_level,
                qb.academic_year

            FROM question_bank qb

            INNER JOIN subject_faculty sf
                ON qb.subject_id = sf.subject_id
               AND qb.faculty_id = sf.faculty_id
               AND qb.academic_year = sf.academic_year

            INNER JOIN subject s
                ON s.subject_id = sf.subject_id

            WHERE sf.academic_year = %s
              AND sf.section_id = %s
              AND s.course_id = %s

            ORDER BY qb.subject_id, qb.question_id
            """,
            (
                student["academic_year"],
                student["section_id"],
                student["course_id"]
            )
        )

        questions = cursor.fetchall()

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
                academic_year
            FROM student
            WHERE student_id = %s
            """,
            (student_id,)
        )

        student = cursor.fetchone()

        if student:
            cursor.execute(
                """
                SELECT DISTINCT
                    s.subject_id,
                    s.subject_name,
                    s.course_id,
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

                ORDER BY s.subject_id
                """,
                (
                    student["academic_year"],
                    student["section_id"],
                    student["course_id"]
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
                academic_year
            FROM student
            WHERE student_id = %s
            """,
            (student_id,)
        )

        student = cursor.fetchone()

        # -----------------------------------------------------
        # GET QUESTIONS (fixed: real column names)
        # -----------------------------------------------------

        if student:

            cursor.execute(
                """
                SELECT DISTINCT
                    qb.question_id,
                    qb.subject_id,
                    qb.question_text,
                    qb.marks,
                    qb.difficulty_level,
                    qb.academic_year

                FROM question_bank qb

                INNER JOIN subject_faculty sf
                    ON qb.subject_id = sf.subject_id
                   AND qb.faculty_id = sf.faculty_id
                   AND qb.academic_year = sf.academic_year

                INNER JOIN subject s
                    ON s.subject_id = sf.subject_id

                WHERE sf.academic_year = %s
                  AND sf.section_id = %s
                  AND s.course_id = %s

                ORDER BY qb.subject_id, qb.question_id
                """,
                (
                    student["academic_year"],
                    student["section_id"],
                    student["course_id"]
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
# STUDENT QUESTION PAPERS
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

        if not student:
            session.clear()
            flash("Student account not found.")
            return redirect(url_for("student_login"))

        # question_papers.subject stores the SUBJECT ID, so join on subject_id.
        cursor.execute(
            """
            SELECT DISTINCT

                qp.id,
                qp.faculty_id,
                qp.subject,
                qp.course,
                qp.total_marks,
                qp.difficulty,
                qp.num_questions,
                qp.pattern,
                qp.created_at

            FROM question_papers qp

            INNER JOIN subject s
                ON qp.subject = s.subject_id

            INNER JOIN subject_faculty sf
                ON s.subject_id = sf.subject_id
               AND qp.faculty_id = sf.faculty_id

            WHERE sf.academic_year = %s
              AND sf.section_id = %s
              AND s.course_id = %s

            ORDER BY qp.created_at DESC
            """,
            (
                student["academic_year"],
                student["section_id"],
                student["course_id"]
            )
        )

        papers = cursor.fetchall()

        # -----------------------------------------------------
        # ADD FIELDS THE TEMPLATE EXPECTS
        # (paper_id, paper_code, duration, year_semester)
        # -----------------------------------------------------

        for paper in papers:

            paper["paper_id"] = paper["id"]

            paper["paper_code"] = f"QP-{paper['id']:04d}"

            paper["duration"] = get_exam_duration(paper["total_marks"])

            course_label = paper.get("course") or student["course_id"] or ""
            year_label = student.get("academic_year") or ""

            paper["year_semester"] = (
                f"{course_label} · {year_label}".strip(" ·")
            )

    except Exception as e:
        print("Student question papers error:", e)
        flash("Unable to load question papers.")

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "student_view_question_papers.html",
        student=student,
        papers=papers
    )

# =========================================================
# VIEW STUDENT QUESTION PAPER
# =========================================================
@app.route("/student/question-paper/<int:paper_id>")
def student_view_question_paper(paper_id):

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
    paper = None

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

        if not student:
            session.clear()
            flash("Student account not found.")
            return redirect(url_for("student_login"))

        cursor.execute(
            """
            SELECT DISTINCT

                qp.id,
                qp.faculty_id,
                qp.subject,
                qp.course,
                qp.total_marks,
                qp.difficulty,
                qp.num_questions,
                qp.pattern,
                qp.syllabus,
                qp.questions,
                qp.created_at

            FROM question_papers qp

            INNER JOIN subject s
                ON qp.subject = s.subject_id

            INNER JOIN subject_faculty sf
                ON s.subject_id = sf.subject_id
               AND qp.faculty_id = sf.faculty_id

            WHERE qp.id = %s
              AND sf.academic_year = %s
              AND sf.section_id = %s
              AND s.course_id = %s
            """,
            (
                paper_id,
                student["academic_year"],
                student["section_id"],
                student["course_id"]
            )
        )

        paper = cursor.fetchone()

    except Exception as e:
        print("View question paper error:", e)
        flash("Unable to open question paper.")

    finally:
        cursor.close()
        connection.close()

    if not paper:
        flash("Question paper not found or you do not have access to it.")
        return redirect(url_for("student_question_papers"))

    return render_template(
        "student_view_question_paper.html",
        student=student,
        paper=paper
    )


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