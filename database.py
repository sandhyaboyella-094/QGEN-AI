import mysql.connector
from mysql.connector import Error
import os
from  dotenv import load_dotenv
load_dotenv()


# =========================================================
# DATABASE CONFIGURATION
# =========================================================

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")


# =========================================================
# CREATE DATABASE
# =========================================================

def create_database():
    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD
        )

        if connection.is_connected():
            cursor = connection.cursor()

            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}`"
            )

            print(f"Database '{DB_NAME}' is ready.")

            cursor.close()
            connection.close()

            return True

    except Error as e:
        print("Database creation error:", e)
        return False

    return False


# =========================================================
# GET DATABASE CONNECTION
# =========================================================

def get_connection():
    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )

        if connection.is_connected():
            return connection

    except Error as e:
        print("Database connection error:", e)

    return None


# =========================================================
# ADD A COLUMN ONLY IF IT IS MISSING
# CREATE TABLE IF NOT EXISTS never changes a table that already
# exists, so existing databases would never get the new columns.
# This helper adds them safely and can be run any number of times.
# =========================================================

def add_column_if_missing(cursor, table, column, definition):

    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column)
    )

    if cursor.fetchone()[0] == 0:
        cursor.execute(
            f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}"
        )
        print(f"Added column {table}.{column}")


# =========================================================
# NEW: STUDENT PROFILE PHOTO COLUMN
# app.py imports and calls this at start-up. initialize_database()
# also adds the column, so running either one is enough.
# =========================================================

def ensure_student_photo_schema():
    """Adds student.profile_image if it doesn't exist yet."""

    connection = get_connection()

    if connection is None:
        return

    cursor = connection.cursor()

    try:
        add_column_if_missing(
            cursor, "student", "profile_image", "VARCHAR(500) NULL"
        )
        connection.commit()

    except Error as e:
        connection.rollback()
        print("Student photo schema error:", e)

    finally:
        cursor.close()
        connection.close()


# =========================================================
# INITIALIZE ALL TABLES
# =========================================================

def initialize_database():

    # -----------------------------------------------------
    # CREATE DATABASE
    # -----------------------------------------------------

    if not create_database():
        return False

    # -----------------------------------------------------
    # CONNECT TO DATABASE
    # -----------------------------------------------------

    connection = get_connection()

    if not connection:
        print("Could not connect to database.")
        return False

    cursor = connection.cursor()

    try:

        # =================================================
        # FACULTY TABLE
        # =================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS faculty (
                faculty_id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                email VARCHAR(150) NOT NULL UNIQUE,
                department VARCHAR(150) NULL,
                year_of_experience INT NOT NULL DEFAULT 0,
                password VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'faculty',
                status VARCHAR(50) NULL DEFAULT 'Active',
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                profile_image VARCHAR(255) NULL
            )
        """)

        # =================================================
        # ADMIN TABLE
        # =================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) NOT NULL UNIQUE,
                email VARCHAR(150) NOT NULL UNIQUE,
                password VARCHAR(255) NOT NULL,
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                profile_image VARCHAR(255) NULL
            )
        """)

        # =================================================
        # PASSWORD RESETS
        # =================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS password_resets (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(150) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'faculty',
                token VARCHAR(255) NOT NULL UNIQUE,
                expires_at DATETIME NOT NULL,
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # =================================================
        # QUESTION PAPERS
        # =================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS question_papers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                faculty_id INT NOT NULL,
                subject VARCHAR(150) NOT NULL,
                course VARCHAR(150) NOT NULL,
                total_marks INT NOT NULL,
                difficulty VARCHAR(50) NOT NULL,
                num_questions INT NOT NULL,
                pattern VARCHAR(50) NOT NULL,
                exam_date DATE NULL,
                syllabus LONGTEXT NULL,
                questions LONGTEXT NULL,
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (faculty_id)
                    REFERENCES faculty(faculty_id)
            )
        """)

        # =================================================
        # SYLLABI
        # =================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS syllabi (
                id INT AUTO_INCREMENT PRIMARY KEY,
                faculty_id INT NOT NULL,
                subject VARCHAR(150) NOT NULL,
                filename VARCHAR(255) NOT NULL,
                filepath VARCHAR(500) NOT NULL,
                syllabus_text LONGTEXT NULL,
                uploaded_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                download_count INT NOT NULL DEFAULT 0,

                FOREIGN KEY (faculty_id)
                    REFERENCES faculty(faculty_id)
            )
        """)

        # =================================================
        # QUESTION BANK
        # =================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS question_bank (
                question_id INT AUTO_INCREMENT PRIMARY KEY,
                subject_id VARCHAR(50) NOT NULL,
                faculty_id INT NOT NULL,
                academic_year VARCHAR(20) NOT NULL,
                question_text TEXT NOT NULL,
                marks INT NOT NULL,
                difficulty_level VARCHAR(50) NOT NULL,

                FOREIGN KEY (faculty_id)
                    REFERENCES faculty(faculty_id)
            )
        """)

        # =================================================
        # FACULTY SETTINGS
        # =================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS faculty_settings (
                faculty_id VARCHAR(50) PRIMARY KEY,
                default_college VARCHAR(255)
                    NULL DEFAULT 'B.M.S College for Women',
                default_course VARCHAR(100)
                    NULL DEFAULT 'BCA',
                default_difficulty VARCHAR(50)
                    NULL DEFAULT 'Medium',
                default_pattern VARCHAR(50)
                    NULL DEFAULT 'Mixed',
                default_num_questions INT
                    NULL DEFAULT 20,
                default_total_marks INT
                    NULL DEFAULT 100,
                theme VARCHAR(20)
                    NULL DEFAULT 'dark',
                notify_paper_generated TINYINT(1)
                    NULL DEFAULT 0,
                notify_syllabus_failed TINYINT(1)
                    NULL DEFAULT 0,
                deletion_requested TINYINT(1)
                    NULL DEFAULT 0
            )
        """)

        # =================================================
        # STUDENT TABLE
        # =================================================

        # CHANGED: added `profile_image VARCHAR(500) NULL` for the
        # student's uploaded profile photo.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS student (
                student_id INT AUTO_INCREMENT PRIMARY KEY,
                uucms_number VARCHAR(50) NULL UNIQUE,
                name VARCHAR(150) NOT NULL,
                email VARCHAR(150) NULL UNIQUE,
                password VARCHAR(255) NOT NULL,
                course_id VARCHAR(50) NOT NULL,
                specialization VARCHAR(100) NULL,
                year_of_study TINYINT NULL,
                semester TINYINT NULL,
                section_id VARCHAR(50) NOT NULL,
                academic_year VARCHAR(20) NOT NULL,
                phone VARCHAR(20) NULL,
                profile_image VARCHAR(500) NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS course (
                course_id VARCHAR(50) PRIMARY KEY,
                course_name VARCHAR(150) NOT NULL,
                department VARCHAR(100) NULL,
                duration_semesters TINYINT NULL
            )
        """)

        # =================================================
        # SUBJECT TABLE
        # =================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subject (
                subject_id VARCHAR(50) NOT NULL PRIMARY KEY,
                subject_name VARCHAR(255) NOT NULL,
                course_id VARCHAR(50) NOT NULL,
                credits INT NOT NULL,
                hours_per_subject INT NOT NULL,
                total_marks INT NOT NULL,
                see_marks INT NOT NULL,
                cia_marks INT NOT NULL,
                cia_duration VARCHAR(50) NULL,
                cia_pattern VARCHAR(100) NULL,
                see_duration VARCHAR(50) NULL,
                see_pattern VARCHAR(100) NULL,
                department VARCHAR(100) NULL,
                semester TINYINT NULL,
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # =================================================
        # SUBJECT FACULTY TABLE
        # =================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subject_faculty (
                subject_id VARCHAR(50) NOT NULL,
                faculty_id INT NOT NULL,
                academic_year VARCHAR(20) NOT NULL,
                section_id VARCHAR(50) NOT NULL,
                student_year INT NULL,

                PRIMARY KEY (
                    subject_id,
                    faculty_id,
                    academic_year,
                    section_id
                ),

                FOREIGN KEY (faculty_id)
                    REFERENCES faculty(faculty_id)
            )
        """)

        # =================================================
        # DEACTIVATION REQUESTS
        # =================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deactivation_requests (
                request_id INT AUTO_INCREMENT PRIMARY KEY,
                faculty_id INT NOT NULL,
                reason TEXT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'Pending',
                requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                decided_at DATETIME NULL,

                INDEX idx_faculty_status (faculty_id, status),

                FOREIGN KEY (faculty_id)
                    REFERENCES faculty(faculty_id)
                    ON DELETE CASCADE
            )
        """)

        # =================================================
        # UPGRADE EXISTING DATABASES
        # The CREATE TABLE statements above only apply to a brand-new
        # database. These make sure older databases get the same
        # columns. Safe to run on every start-up.
        # =================================================

        add_column_if_missing(cursor, "student", "year_of_study", "TINYINT NULL")
        add_column_if_missing(cursor, "student", "semester", "TINYINT NULL")
        add_column_if_missing(cursor, "student", "profile_image", "VARCHAR(500) NULL")

        add_column_if_missing(cursor, "subject", "department", "VARCHAR(100) NULL")
        add_column_if_missing(cursor, "subject", "semester", "TINYINT NULL")

        add_column_if_missing(cursor, "subject_faculty", "student_year", "INT NULL")

        # =================================================
        # COMMIT
        # =================================================

        connection.commit()

        print("All tables are ready.")

        return True

    except Error as e:

        connection.rollback()

        print("Table creation error:", e)

        return False

    finally:

        cursor.close()
        connection.close()


# =========================================================
# RUN DATABASE INITIALIZATION
# =========================================================

if __name__ == "__main__":

    print("=" * 60)
    print("AI QUESTION PAPER GENERATOR")
    print("DATABASE SETUP")
    print("=" * 60)

    if initialize_database():

        print()
        print(f"Database : {DB_NAME}")

        print()
        print("=" * 60)
        print("DATABASE SETUP COMPLETED")
        print("=" * 60)

    else:

        print()
        print("=" * 60)
        print("DATABASE SETUP FAILED")
        print("=" * 60)