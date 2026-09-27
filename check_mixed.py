"""
check_mixed.py - shows which updates each of your .py files already has.

USAGE: put this file in your project folder and run:   python check_mixed.py
"""

import glob
import os
import re


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def mixed_status(text):
    has_new = "def create_mixed_questions(" in text
    if has_new and "_pick_fresh_question" in text and "MIXED_PATTERN_TABLE" in text \
            and "MIXED_C_SPLIT_SHARE" in text and "mixed_sections=" in text:
        return "UP TO DATE (2/5/10 marks, any total, direct or split Part C, topics reused)", None
    if has_new:
        return "OLDER VERSION of the ported Mixed logic", "port_mixed.py"
    if "def get_mixed_pattern(" in text:
        return "app2-style (80 marks only, not the latest)", "port_mixed.py"
    return "OLD Mixed logic (this is what gives 1, 3, 4, 6, 7 marks)", "port_mixed.py"


files = sorted(
    p for p in glob.glob("*.py")
    if os.path.basename(p) != os.path.basename(__file__)
)

todo = {}
found_any = False

print("Python files that generate question papers:\n")

for path in files:
    text = read(path)

    if "def create_questions(" not in text:
        continue

    found_any = True
    runs_flask = bool(re.search(r"app\.run\(", text))

    status, fix = mixed_status(text)
    auto_ok = "def auto_question_count(" in text
    flat_ok = "def create_flat_questions(" in text

    print(f"  {path}" + ("   <- has app.run(), can be started with python" if runs_flask else ""))
    print(f"     Mixed pattern            : {status}")
    print(f"     Automatic no. of questions: {'yes' if auto_ok else 'NO'}")
    print(f"     Topic reuse (MCQ/Short/Medium/Long): {'yes' if flat_ok else 'NO'}")
    print()

    missing = []
    if fix:
        missing.append("python port_mixed.py " + path)
    if not auto_ok:
        missing.append("python auto_question_count.py " + path)
    if not flat_ok:
        missing.append("python reuse_topics_flat.py " + path)
    if missing and runs_flask:
        todo[path] = missing

if not found_any:
    print("  none found in this folder - run this from your project folder.")

if todo:
    print("Commands still to run (only for the file you start with 'python <name>.py'):\n")
    for path, commands in todo.items():
        print(f"  {path}:")
        for command in commands:
            print(f"     {command}")
        print()
    print("Run them one after another, then stop Flask (Ctrl+C) and start it again.")
else:
    print("Every file that has app.run() is fully up to date.")