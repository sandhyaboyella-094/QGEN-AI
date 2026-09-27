"""
auto_question_count.py - the number of questions is worked out automatically
from the Total Marks and the Question Pattern (no more typing it).

    MCQ 1 mark each | Short 2 | Medium 5 | Long 10 | AI 5 | Mixed: fixed
    structure per total (e.g. 80 marks = 12 + 5 + 5 questions given)

USAGE (in your project folder):
    python auto_question_count.py                       -> patches app_ai.py and
                                                           templates/generate_question_paper.html
    python auto_question_count.py my_app.py             -> patches another app file
    python auto_question_count.py my_app.py path/to/generate_question_paper.html

Backups are saved as <file>.bak. If a marker is missing the script stops
without changing anything. It only patches what is still missing, so it is
safe to run again if the form was replaced by an older copy.
"""

import os
import re
import sys
import shutil
import tempfile
import py_compile

APP_FILE = sys.argv[1] if len(sys.argv) > 1 else "app_ai.py"

FORM_FILE = (
    sys.argv[2]
    if len(sys.argv) > 2
    else os.path.join(os.path.dirname(os.path.abspath(APP_FILE)), "templates", "generate_question_paper.html")
)

BLOCK = r'''# =========================================================
# AUTOMATIC NUMBER OF QUESTIONS
#
# The faculty no longer types the number of questions. It is worked out
# from the Total Marks and the Question Pattern:
#
#   MCQ     1 mark per question    (80 marks -> 80 questions)
#   Short   2 marks per question   (80 marks -> 40 questions)
#   Medium  5 marks per question   (80 marks -> 16 questions)
#   Long   10 marks per question   (80 marks ->  8 questions)
#   AI      5 marks per question   (80 marks -> 16 questions)
#   Mixed   fixed structure per total (see MIXED_PATTERN_TABLE):
#           e.g. 80 marks -> 12 + 5 + 5 = 22 questions given
#
# Change MARKS_PER_QUESTION below if your college uses different weights.
# =========================================================

MARKS_PER_QUESTION = {
    "MCQ": 1,
    "Short": 2,
    "Medium": 5,
    "Long": 10,
    "AI": 5,
}


def auto_question_count(pattern, total_marks):
    """
    Returns (count, description, error_message). Exactly one of
    count / error_message is None.
    """

    try:
        total_marks = int(total_marks)
    except (TypeError, ValueError):
        return None, "", "Please select the total marks."

    if pattern == "Mixed":

        given = (
            get_mixed_given_counts(total_marks)
            if "get_mixed_given_counts" in globals()
            else None
        )

        attempt = (
            get_mixed_section_counts(total_marks)
            if "get_mixed_section_counts" in globals()
            else None
        )

        if not given or not attempt:
            return None, "", (
                "Mixed pattern needs a Total Marks value that is a multiple "
                "of 5 (30 or more), for example 50, 60, 70, 80 or 100."
            )

        description = (
            f"{sum(given.values())} questions: "
            f"Part A {given['Part A']} x 2 marks (answer {attempt['Part A']}), "
            f"Part B {given['Part B']} x 5 marks (answer {attempt['Part B']}), "
            f"Part C {given['Part C']} x 10 marks (answer {attempt['Part C']})"
        )

        return sum(given.values()), description, None

    per_question = MARKS_PER_QUESTION.get(pattern)

    if per_question is None:
        return None, "", "Please select a valid question pattern."

    if total_marks < per_question or total_marks % per_question:
        return None, "", (
            f"{total_marks} marks cannot be split into questions of "
            f"{per_question} marks each. Please choose a different "
            "Total Marks value or pattern."
        )

    count = total_marks // per_question

    description = (
        f"{count} question{'s' if count != 1 else ''} x "
        f"{per_question} mark{'s' if per_question != 1 else ''} each"
    )

    return count, description, None


@app.route("/faculty/question-count")
def faculty_question_count():
    """Used by the generate form to show the automatic question count."""

    def reply(payload, status=200):
        return app.response_class(
            json.dumps(payload),
            status=status,
            mimetype="application/json"
        )

    if not faculty_required():
        return reply({"ok": False, "error": "Please login again."}, 401)

    count, description, error = auto_question_count(
        request.args.get("pattern", ""),
        request.args.get("total_marks", "")
    )

    if error:
        return reply({"ok": False, "error": error})

    return reply({"ok": True, "count": count, "description": description})
'''

FORM_SNIPPET = r'''                <label>Number of Questions <span class="auto-tag">(automatic)</span></label>
                <input type="number" name="num_questions" id="num_questions" value="" readonly
                       placeholder="Calculated from Total Marks and Pattern">
                <div class="auto-hint" id="count_hint">
                    Choose the Total Marks and the Question Pattern.
                </div>
'''

FORM_SCRIPT = r'''    <style>
        .auto-tag {
            font-weight: 600;
            color: var(--maroon);
        }
        #num_questions {
            background: rgba(130,25,58,.06);
            cursor: not-allowed;
        }
        .auto-hint {
            margin-top: 6px;
            font-size: 12.5px;
            font-weight: 600;
            color: var(--dark-maroon);
        }
        .auto-hint.error {
            color: #b91c1c;
        }
    </style>

    <script>
        // Number of questions is worked out on the server from Total Marks
        // and Question Pattern; this only shows it to the faculty.
        (function () {
            var marks = document.querySelector('[name="total_marks"]');
            var pattern = document.querySelector('[name="pattern"]');
            var count = document.getElementById('num_questions');
            var hint = document.getElementById('count_hint');

            if (!marks || !pattern || !count || !hint) { return; }

            function update() {
                if (!marks.value || !pattern.value) {
                    count.value = '';
                    hint.className = 'auto-hint';
                    hint.textContent = 'Choose the Total Marks and the Question Pattern.';
                    return;
                }

                fetch('/faculty/question-count?total_marks=' + encodeURIComponent(marks.value) +
                      '&pattern=' + encodeURIComponent(pattern.value), { credentials: 'same-origin' })
                    .then(function (response) { return response.json(); })
                    .then(function (data) {
                        if (data.ok) {
                            count.value = data.count;
                            hint.className = 'auto-hint';
                            hint.textContent = data.description;
                        } else {
                            count.value = '';
                            hint.className = 'auto-hint error';
                            hint.textContent = data.error || 'Could not work out the number of questions.';
                        }
                    })
                    .catch(function () {
                        count.value = '';
                        hint.className = 'auto-hint';
                        hint.textContent = 'The number of questions is calculated automatically when you generate the paper.';
                    });
            }

            marks.addEventListener('change', update);
            pattern.addEventListener('change', update);
            update();
        })();
    </script>
'''


def fail(message):
    print("PATCH STOPPED (nothing was changed):", message)
    sys.exit(1)


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        fail(f"expected exactly 1 match for {label}, found {count}.")
    return text.replace(old, new, 1)


def banner_before(text, title):
    marker = "\n# " + title + "\n"
    if text.count(marker) != 1:
        fail(f"could not find the '# {title}' banner exactly once.")
    idx = text.find(marker)
    banner = text.rfind("# =====", 0, idx + 1)
    if banner == -1:
        fail(f"could not locate the banner line above '# {title}'.")
    return banner


def patch_app(text):

    if "def auto_question_count(" in text:
        fail(f"{APP_FILE} already has the automatic question count.")

    # 1. helper + /faculty/question-count endpoint, above the generate route
    banner = banner_before(text, "GENERATE QUESTION PAPER")
    text = text[:banner] + BLOCK.strip("\n") + "\n\n\n" + text[banner:]

    # 2. in the route, work the count out from marks + pattern
    limit_marker = (
        "        # =================================================\n"
        "        # QUESTION LIMIT\n"
    )
    auto_code = (
        "        # =================================================\n"
        "        # AUTOMATIC NUMBER OF QUESTIONS\n"
        "        # (from Total Marks and Question Pattern - the value\n"
        "        # typed in the form, if any, is ignored)\n"
        "        # =================================================\n\n"
        "        auto_count, _count_note, count_error = auto_question_count(\n"
        "            pattern,\n"
        "            total_marks_int\n"
        "        )\n\n"
        "        if count_error:\n\n"
        "            flash(\n"
        "                count_error,\n"
        '                "error"\n'
        "            )\n\n"
        "            return redirect(\n"
        '                url_for("generate_question_paper")\n'
        "            )\n\n"
        "        num_questions = auto_count\n\n"
    )
    text = replace_once(text, limit_marker, auto_code + limit_marker, "the QUESTION LIMIT banner in the route")

    # 3. safety: a flat paper must have exactly the questions its marks need
    empty_check = (
        "        if not questions:\n\n"
        "            flash(\n"
        '                "Unable to generate questions."\n'
        "            )\n\n"
        "            return redirect(\n"
        '                url_for("generate_question_paper")\n'
        "            )\n"
    )
    guard = (
        "\n"
        "        # A paper whose questions are fewer than its marks need would\n"
        "        # print the wrong total, so stop and tell the faculty why.\n"
        '        if pattern != "AI" and len(questions) != num_questions:\n\n'
        "            flash(\n"
        '                f"The syllabus could only produce {len(questions)} of the "\n'
        '                f"{num_questions} questions needed for {total_marks_int} marks "\n'
        '                f"in the {pattern} pattern. Please upload a more detailed "\n'
        '                "syllabus or choose a different pattern.",\n'
        '                "error"\n'
        "            )\n\n"
        "            return redirect(\n"
        '                url_for("generate_question_paper")\n'
        "            )\n"
    )
    text = replace_once(text, empty_check, empty_check + guard, "the 'Unable to generate questions' check")

    return text


def patch_form(html):

    if 'id="num_questions"' in html:
        fail(f"{FORM_FILE} already has the automatic question count.")

    pattern = re.compile(
        r'<label>\s*Number of Questions\s*</label>\s*<input[^>]*name="num_questions"[^>]*>',
        re.IGNORECASE
    )

    matches = pattern.findall(html)

    if len(matches) != 1:
        fail(f"could not find the 'Number of Questions' field exactly once in {FORM_FILE}.")

    html = pattern.sub(lambda m: FORM_SNIPPET.strip("\n"), html, count=1)

    if html.count("</body>") != 1:
        fail(f"could not find </body> exactly once in {FORM_FILE}.")

    html = html.replace("</body>", FORM_SCRIPT.strip("\n") + "\n\n</body>", 1)

    return html


def compile_check(text, label):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp:
        tmp.write(text)
        path = tmp.name
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as exc:
        os.unlink(path)
        fail(f"the patched {label} did not compile, so nothing was changed.\n" + str(exc))
    os.unlink(path)


def main():

    for path in (APP_FILE, FORM_FILE):
        if not os.path.exists(path):
            fail(f"{path} not found.")

    with open(APP_FILE, "r", encoding="utf-8") as f:
        app_text = f.read()

    with open(FORM_FILE, "r", encoding="utf-8") as f:
        form_html = f.read()

    app_done = "def auto_question_count(" in app_text
    form_done = 'id="num_questions"' in form_html

    if app_done and form_done:
        fail("both the app file and the form already have the automatic question count.")

    # patch only what is still missing (for example, if the form was
    # replaced by an older copy after the app file had been patched)
    new_app = app_text if app_done else patch_app(app_text)

    if not app_done:
        compile_check(new_app, APP_FILE)

    new_form = form_html if form_done else patch_form(form_html)

    if not app_done:
        shutil.copyfile(APP_FILE, APP_FILE + ".bak")
        with open(APP_FILE, "w", encoding="utf-8") as f:
            f.write(new_app)
        print(f"Updated {APP_FILE} (backup saved as {APP_FILE}.bak).")
    else:
        print(f"{APP_FILE} already had the automatic question count - left as it is.")

    if not form_done:
        shutil.copyfile(FORM_FILE, FORM_FILE + ".bak")
        with open(FORM_FILE, "w", encoding="utf-8") as f:
            f.write(new_form)
        print(f"Updated {FORM_FILE} (backup saved as {FORM_FILE}.bak).")
    else:
        print(f"{FORM_FILE} already had the automatic field - left as it is.")

    print("Restart Flask (Ctrl+C, then start it again) and refresh the page with Ctrl+F5.")
    print("Choose Total Marks and Question Pattern - the number of questions fills in by itself.")


if __name__ == "__main__":
    main()