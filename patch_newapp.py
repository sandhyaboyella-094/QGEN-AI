"""
Usage:  python patch_newapp.py newapp.py
Creates newapp_updated.py next to it (your original is not touched).
Requires question_engine.py (latest version) in the same folder as newapp.py.
"""
import os
import py_compile
import sys

src = sys.argv[1] if len(sys.argv) > 1 else "newapp.py"
dst = os.path.join(os.path.dirname(os.path.abspath(src)), "newapp_updated.py")

with open(src, "r", encoding="utf-8") as f:
    code = f.read()


def fail(msg):
    print("PATCH FAILED:", msg)
    print("Nothing was written. Send me the section named above and I will adjust the patch.")
    sys.exit(1)


# ------------------------------------------------------------------ 1. import
if "import question_engine" not in code:
    anchor = "from pypdf import PdfReader\n"
    if anchor not in code:
        fail("could not find 'from pypdf import PdfReader'")
    code = code.replace(anchor, anchor + "\nimport question_engine\n", 1)

# ------------------------------------------- 2. replace create_questions()
NEW_CREATE = '''def create_questions(subject, syllabus, number, pattern, difficulty, total_marks):
    """Thin wrapper: all question formation lives in question_engine.py."""
    topics_by_unit = _extract_topics_by_unit(syllabus, subject)

    if not topics_by_unit:
        topics_by_unit = {0: [subject] + FALLBACK_TOPICS}

    return question_engine.generate(subject, topics_by_unit, pattern, int(total_marks))


'''

start = code.find("def create_questions(")
end_marker = "# =========================================================\n# GENERATE QUESTION PAPER"
end = code.find(end_marker)
if start == -1 or end == -1 or end < start:
    fail("could not locate create_questions() or the 'GENERATE QUESTION PAPER' banner")
code = code[:start] + NEW_CREATE + code[end:]

# ------------------- 3. replace the mixed-counts block with question_engine.plan
NEW_PLAN = '''    try:
        paper_plan = question_engine.plan(pattern, total_marks_int)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("generate_question_paper"))

    # Number of questions always comes from marks-per-pattern (form value is ignored):
    # MCQ 1m, Short 2m, Medium 5m, Long 10m, Mixed 2m/5m/10m.
    num_questions = paper_plan["number"]

    mixed_counts = {}
    mixed_given_counts = {}
    mixed_sections = []

    if paper_plan["parts"]:
        mixed_counts = {k: v["attempt"] for k, v in paper_plan["parts"].items()}
        mixed_given_counts = {k: v["given"] for k, v in paper_plan["parts"].items()}
        mixed_sections = [
            s for s in MIXED_SECTIONS if s["name"] in paper_plan["parts"]
        ]

'''

block_start_marker = "    mixed_counts = {}\n    mixed_given_counts = {}\n    mixed_sections = []\n"
block_end_marker = "    questions = create_questions("
bs = code.find(block_start_marker)
be = code.find(block_end_marker, bs if bs != -1 else 0)
if bs == -1 or be == -1:
    fail("could not locate the 'mixed_counts = {}' block in generate_question_paper()")
code = code[:bs] + NEW_PLAN + code[be:]

# ------------------------------- 4. pass the subject NAME, not the subject id
old_call = "questions = create_questions(\n        subject=subject_id,"
new_call = "questions = create_questions(\n        subject=subject_name,"
if old_call not in code:
    fail("could not find the create_questions(...) call with subject=subject_id")
code = code.replace(old_call, new_call, 1)

# ------------- 5. show engine errors as a flash message instead of a 500 page
call_start = code.find("    questions = create_questions(")
call_end = code.find("\n    )\n", call_start)
if call_start == -1 or call_end == -1:
    fail("could not locate the full create_questions(...) call")
call_end += len("\n    )\n")
call_block = code[call_start:call_end]
wrapped = (
    "    try:\n"
    + "".join("    " + line + "\n" for line in call_block.rstrip("\n").split("\n"))
    + "    except ValueError as e:\n"
    + "        flash(str(e), \"error\")\n"
    + "        return redirect(url_for(\"generate_question_paper\"))\n"
)
code = code[:call_start] + wrapped + code[call_end:]

# ---------------------------------------------------------------- write+check
with open(dst, "w", encoding="utf-8") as f:
    f.write(code)

try:
    py_compile.compile(dst, doraise=True)
except py_compile.PyCompileError as e:
    os.remove(dst)
    fail(f"patched file did not compile: {e}")

print(f"OK -> {dst}")
print("Next: make sure the latest question_engine.py is in the same folder,")
print("then rename newapp_updated.py to newapp.py (keep a backup) and restart Flask.")