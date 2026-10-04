"""
apppatch.py - applies the Gemini fixes to app.py

Usage (run in the same folder as app.py):
    python apppatch.py            # patches app.py, keeps a backup app.py.bak
    python apppatch.py path/to/app.py

Nothing is written unless every patch applies cleanly.
Running it twice is safe (already-applied patches are skipped).
"""

import re
import shutil
import sys

TARGET = sys.argv[1] if len(sys.argv) > 1 else "app_mixed1.py"


# ---------------------------------------------------------------------
# Patch 1: default generation mode -> gemini
# ---------------------------------------------------------------------
def patch_default_mode(src):
    old = 'generation_mode = request.form.get("generation_mode", "template").strip().lower()'
    new = 'generation_mode = request.form.get("generation_mode", "gemini").strip().lower()'

    if new in src:
        return src, "skipped (already applied)"
    if old not in src:
        raise RuntimeError("default generation_mode line not found")

    return src.replace(old, new, 1), "applied"


# ---------------------------------------------------------------------
# Patch 2: no silent fallback to templates
# ---------------------------------------------------------------------
NEW_GENERATION_BLOCK = r'''    if generation_mode not in ("template", "gemini"):
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
            print("Gemini generation error:", e)
            flash(f"Gemini could not generate this paper: {e}. Please try again.", "error")
            return redirect(url_for("generate_question_paper"))

        except Exception as e:
            print("Gemini generation error:", repr(e))
            flash("Gemini could not generate this paper. Please try again.", "error")
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

'''


def patch_generation_block(src):
    if "Gemini could not generate this paper: {e}. Please try again." in src:
        return src, "skipped (already applied)"

    start_marker = '    if generation_mode not in ("template", "gemini"):'
    end_marker = '    if not questions:\n        flash("Unable to generate questions."'

    start = src.find(start_marker)
    if start == -1:
        raise RuntimeError("generation block start not found")

    end = src.find(end_marker, start)
    if end == -1:
        raise RuntimeError("generation block end not found")

    return src[:start] + NEW_GENERATION_BLOCK + src[end:], "applied"


# ---------------------------------------------------------------------
# Patch 3: last line of QUESTION_FORMATION_RULES (prompt asks for JSON)
# ---------------------------------------------------------------------
def patch_rules_ending(src):
    old = "Return only the final examination question.\n"
    new = (
        "Return only the JSON described below. Each \"text\" field must contain\n"
        "only the final examination question, with no numbering or commentary.\n"
    )

    if "Return only the JSON described below." in src:
        return src, "skipped (already applied)"
    if src.count(old) != 1:
        raise RuntimeError(
            f"expected 1 'Return only the final examination question.' line, found {src.count(old)}"
        )

    return src.replace(old, new, 1), "applied"


# ---------------------------------------------------------------------
# Patch 4a: Part B / Part C prompt wording
# ---------------------------------------------------------------------
NEW_PART_BC = r'''+ f"Part B: exactly {plan['Part B']['given']} questions of 5 marks each. "
          "Each needs a structured answer of about 150-200 words, in the form "
          "the topic naturally calls for (explain a concept, explain with a "
          "diagram, describe a process, explain the working, compare two named "
          "concepts, advantages and limitations, or a short note). "
          "bloom_level is the level that honestly describes the question "
          "(Understand, Apply or Analyze). Do not force a level the topic "
          "does not support. "
          f"The unit of each question, in order, must be: {unit_order('Part B')}.\n"
        + f"Part C: exactly {plan['Part C']['given']} items, each with exactly 2 "
          "sub_questions of 5 marks each. Each sub-question is a substantial "
          "descriptive question in the form the topic naturally calls for "
          "(detailed explanation, working or algorithm, detailed comparison of "
          "two named concepts, or structured discussion). Use Design, Derive, "
          "Evaluate or Write a program ONLY if the syllabus topic itself "
          "involves that. bloom_level is Understand, Apply, Analyze, Evaluate "
          "or Create, whichever truly fits. Both sub-questions of an item come "
          "from the SAME unit but cover different topics, and neither depends "
          "on the other. "
          f"The unit of each item, in order, must be: {unit_order('Part C')}.\n"'''


def patch_part_bc_prompt(src):
    if "Do not force a level the topic" in src:
        return src, "skipped (already applied)"

    pattern = re.compile(
        r'\+ f"Part B: exactly.*?\{unit_order\(\'Part C\'\)\}\.\\n"',
        re.DOTALL
    )
    match = pattern.search(src)

    if not match:
        raise RuntimeError("Part B / Part C prompt lines not found")

    return src[:match.start()] + NEW_PART_BC + src[match.end():], "applied"


# ---------------------------------------------------------------------
# Patch 4b: allow all Bloom levels in Part C
# ---------------------------------------------------------------------
def patch_part_c_levels(src):
    old = '"bloom_levels": ["Analyze", "Evaluate", "Create"]'
    new = '"bloom_levels": ["Understand", "Apply", "Analyze", "Evaluate", "Create"]'

    if new in src:
        return src, "skipped (already applied)"
    if src.count(old) != 1:
        raise RuntimeError(f"expected 1 Part C bloom_levels line, found {src.count(old)}")

    return src.replace(old, new, 1), "applied"


# ---------------------------------------------------------------------
# Patch 5: log when Gemini is called
# ---------------------------------------------------------------------
def patch_logging(src):
    anchor = '    model = os.environ.get("GEMINI_MODEL", "").strip() or GEMINI_DEFAULT_MODEL\n'
    log_line = '    print(f"[Gemini] calling model {model}")\n'

    if log_line in src:
        return src, "skipped (already applied)"
    if src.count(anchor) != 1:
        raise RuntimeError("Gemini model line not found")

    return src.replace(anchor, anchor + "\n" + log_line, 1), "applied"


PATCHES = [
    ("1. default mode = gemini", patch_default_mode),
    ("2. no silent template fallback", patch_generation_block),
    ("3. rules ending matches JSON output", patch_rules_ending),
    ("4a. Part B / Part C prompt wording", patch_part_bc_prompt),
    ("4b. Part C Bloom levels", patch_part_c_levels),
    ("5. Gemini call logging", patch_logging),
]


def main():
    try:
        with open(TARGET, "r", encoding="utf-8", newline="") as f:
            raw = f.read()
    except OSError as e:
        print(f"Cannot read {TARGET}: {e}")
        sys.exit(1)

    crlf = "\r\n" in raw
    src = raw.replace("\r\n", "\n")

    for name, patch in PATCHES:
        try:
            src, status = patch(src)
        except RuntimeError as e:
            print(f"FAILED  {name}: {e}")
            print("No changes were written.")
            sys.exit(1)

        print(f"{status:<26} {name}")

    if crlf:
        src = src.replace("\n", "\r\n")

    if src == raw:
        print("Nothing to change.")
        return

    shutil.copyfile(TARGET, TARGET + ".bak")

    with open(TARGET, "w", encoding="utf-8", newline="") as f:
        f.write(src)

    print(f"\nDone. Backup saved as {TARGET}.bak")


if __name__ == "__main__":
    main()