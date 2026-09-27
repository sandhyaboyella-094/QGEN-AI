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
                status VARCHAR(50) NULL DEFAULT 'current_status',
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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS student (
                student_id INT AUTO_INCREMENT PRIMARY KEY,
                uucms_number VARCHAR(50) NULL UNIQUE,
                name VARCHAR(150) NOT NULL,
                email VARCHAR(150) NULL UNIQUE,
                password VARCHAR(255) NOT NULL,
                course_id VARCHAR(50) NOT NULL,
                section_id VARCHAR(50) NOT NULL,
                academic_year VARCHAR(20) NOT NULL
            )
        """)

        # =================================================
        # SUBJECT TABLE
        # =================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subject (
                course_id VARCHAR(50) NOT NULL,
                subject_id VARCHAR(50) NOT NULL UNIQUE,
                credits INT NOT NULL,
                total_marks INT NOT NULL,
                see_marks INT NOT NULL,
                cia_marks INT NOT NULL,
                hours_per_subject INT NOT NULL,

                PRIMARY KEY (course_id)
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
        print("ADMIN LOGIN")
        print("Email    : admin@gmail.com")
        print("Password : admin123")

        print()
        print("FACULTY LOGIN")
        print("Email    : faculty@gmail.com")
        print("Password : faculty123")

        print()
        print("=" * 60)
        print("DATABASE SETUP COMPLETED")
        print("=" * 60)

    else:

        print()
        print("=" * 60)
        print("DATABASE SETUP FAILED")
        print("=" * 60)