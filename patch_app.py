"""
patch_app.py - upgrades your existing app.py so AI works with EVERY
question pattern (MCQ / Short / Medium / Long / Mixed), with two
independent choices on the form:

    generation_mode : "template" (Without AI)  |  "ai" (With AI)
    pattern         : MCQ | Short | Medium | Long | Mixed

USAGE (Windows / any OS):
    1. Put this file in the same folder as app.py
    2. Run:   python patch_app.py
       (or:   python patch_app.py path\\to\\app.py)
    3. A backup is saved as app.py.bak, and app.py is updated in place.

Safe to run once. If it can't find an expected marker it stops WITHOUT
changing anything.
"""

import sys
import re
import shutil
import py_compile
import tempfile
import os

TARGET = sys.argv[1] if len(sys.argv) > 1 else "app.py"

AI_SECTION = r'''# =========================================================
# AI QUESTION GENERATION - PATTERN AWARE, MULTI-PROVIDER
#
# Works with EVERY pattern (MCQ / Short / Medium / Long / Mixed).
# The faculty chooses "With AI" or "Without AI (Template)" in the
# form (generation_mode); the pattern is chosen independently.
#
# Providers are tried in AI_PROVIDER_ORDER (default: gemini).
# Optional backup: set AI_PROVIDER_ORDER=gemini,openai together
# with OPENAI_API_KEY (and `pip install openai`).
# =========================================================

import time

AI_PROVIDER_ORDER = [
    provider_name.strip()
    for provider_name in os.environ.get(
        "AI_PROVIDER_ORDER",
        "gemini"
    ).split(",")
    if provider_name.strip()
]

OPENAI_API_KEY = os.environ.get(
    "OPENAI_API_KEY",
    ""
)

OPENAI_MODEL = os.environ.get(
    "OPENAI_MODEL",
    "gpt-5-mini"
)


def provider_available(name):

    if name == "gemini":

        return get_gemini_client() is not None

    if name == "openai":

        return bool(OPENAI_API_KEY)

    return False


def call_gemini(prompt):

    client = get_gemini_client()

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=gemini_types.GenerateContentConfig(
            response_mime_type="application/json",
            http_options=gemini_types.HttpOptions(
                timeout=GEMINI_REQUEST_TIMEOUT_MS
            )
        )
    )

    return response.text


def call_openai(prompt):

    from openai import OpenAI

    client = OpenAI(
        api_key=OPENAI_API_KEY,
        timeout=45
    )

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        response_format={
            "type": "json_object"
        }
    )

    return response.choices[0].message.content


def classify_ai_exception(exception):

    if (
        GEMINI_SDK_AVAILABLE
        and
        isinstance(
            exception,
            gemini_errors.APIError
        )
    ):

        return classify_gemini_exception(exception)

    code = getattr(
        exception,
        "status_code",
        None
    )

    if code == 429:

        return "rate_limit"

    if code in (401, 403):

        return "auth"

    if code is not None and code >= 500:

        return "server"

    return "other"


def call_ai_with_fallback(prompt):
    """
    Tries each configured provider in order. If every provider
    fails with a rate limit, waits once (per-minute limits reset
    quickly) and tries the whole list again.
    Returns (raw_text, error_reason).
    """

    usable_providers = [
        name
        for name in AI_PROVIDER_ORDER
        if provider_available(name)
    ]

    if not usable_providers:

        return None, "unavailable"

    last_reason = "other"

    for attempt in range(2):

        all_rate_limited = True

        for name in usable_providers:

            try:

                if name == "gemini":

                    return call_gemini(prompt), None

                return call_openai(prompt), None

            except Exception as e:

                print(
                    f"{name} error:",
                    e
                )

                last_reason = classify_ai_exception(e)

                if last_reason != "rate_limit":

                    all_rate_limited = False

        if attempt == 0 and all_rate_limited:

            time.sleep(20)

            continue

        break

    return None, last_reason


# ---------------------------------------------------------
# PER-PATTERN INSTRUCTIONS + THE JSON SHAPE THE AI MUST RETURN
# ---------------------------------------------------------

AI_PATTERN_RULES = {

    "MCQ": {

        "style": (
            "Write multiple choice questions. Each has exactly 4 "
            "options, only ONE correct, and 3 plausible distractors. "
            "Never use 'All of these' or 'None of these'."
        ),

        "shape": (
            '{"text": "<question>", "unit": <int>, '
            '"options": ["<A>", "<B>", "<C>", "<D>"], '
            '"answer_index": <0-3>}'
        )

    },

    "Short": {

        "style": (
            "Write SHORT-answer questions (2-3 mark style): define, "
            "state, list, or briefly explain. Answerable in 2-4 "
            "sentences."
        ),

        "shape": '{"text": "<question>", "unit": <int>}'

    },

    "Medium": {

        "style": (
            "Write MEDIUM-answer questions (5 mark style): explain, "
            "describe, or illustrate with an example. Answerable in "
            "one or two paragraphs."
        ),

        "shape": '{"text": "<question>", "unit": <int>}'

    },

    "Long": {

        "style": (
            "Write LONG-answer questions (10 mark style): discuss in "
            "detail, analyze, compare, critically evaluate, or "
            "design. They need multi-part, essay-style answers."
        ),

        "shape": '{"text": "<question>", "unit": <int>}'

    }

}

AI_MIXED_DEPTH = {

    "Part A": "Short-answer depth (about 2 marks): recall and basic understanding.",

    "Part B": "Medium-answer depth (about 5 marks): explanation, application, analysis.",

    "Part C": "Long-answer depth (about 10 marks): analysis, evaluation, design."

}


def build_ai_prompt(
    subject,
    course,
    syllabus_unit_context,
    difficulty,
    pattern,
    count_needed,
    already_used_texts,
    section=None
):

    avoid_block = ""

    if already_used_texts:

        used_list = "\n".join(
            f"- {text}"
            for text in already_used_texts
        )

        avoid_block = f"""

These questions have ALREADY been written - do not repeat them or write near-duplicates:
{used_list}
"""

    if pattern == "Mixed" and section:

        style = (
            f"This is {section['name']} of a mixed-pattern paper. "
            f"{AI_MIXED_DEPTH[section['name']]} "
            f"Each question must target a Bloom's taxonomy level - "
            f"mostly from [{', '.join(section['bloom_levels'])}], "
            f"occasionally from [{', '.join(section['overlap_levels'])}]."
        )

        shape = (
            '{"text": "<question>", "unit": <int>, '
            '"bloom_level": "<Remember|Understand|Apply|Analyze|Evaluate|Create>"}'
        )

    else:

        style = AI_PATTERN_RULES[pattern]["style"]

        shape = AI_PATTERN_RULES[pattern]["shape"]

    difficulty_note = (
        "Blend easy, medium and hard questions."
        if difficulty.lower() == "mixed"
        else f"Match the requested difficulty: {difficulty}."
    )

    return f"""You are writing exam questions for a college course.

Subject: {subject}
Course: {course}
Difficulty: {difficulty}
Number of questions required: {count_needed}

The syllabus, broken into units, is:
{syllabus_unit_context}
{avoid_block}
Write exactly {count_needed} questions.

Question style: {style}

Rules:
- Spread questions as evenly as possible across every unit listed above.
- Each question must be complete, self-contained and grammatically correct.
- If you write a comparison question, name BOTH real topics from the syllabus - never "a related concept".
- {difficulty_note}
- Do not invent topics that are not in the syllabus above.
- Do not include marks or numbering in the question text.

Return ONLY a JSON object of this form (JSON, no other text):
{{"questions": [ {shape}, ... exactly {count_needed} items ]}}"""


def request_ai_question_batch(
    prompt,
    pattern
):
    """
    One AI call (with provider fallback). Returns
    (items, error_reason). Items that do not fit the pattern
    (e.g. an MCQ without 4 distinct options) are dropped so the
    top-up round can replace them.
    """

    raw_text, error_reason = call_ai_with_fallback(
        prompt
    )

    if raw_text is None:

        return [], error_reason

    try:

        parsed = json.loads(
            raw_text
        )

    except ValueError:

        return [], "other"

    raw_items = (
        parsed.get("questions")
        if isinstance(parsed, dict)
        else parsed
    )

    if not isinstance(raw_items, list):

        return [], "other"

    cleaned_items = []

    seen_in_batch = set()

    for item in raw_items:

        if not isinstance(item, dict):

            continue

        text = str(
            item.get(
                "text",
                ""
            )
        ).strip()

        if not text or text.lower() in seen_in_batch:

            continue

        try:

            unit_number = max(
                int(item.get("unit", 1)),
                1
            )

        except (TypeError, ValueError):

            unit_number = 1

        cleaned = {

            "text": text,

            "unit": f"Unit {unit_number}"

        }

        # ---- MCQ: validate options + answer ----

        if pattern == "MCQ":

            options = item.get("options")

            if not isinstance(options, list) or len(options) != 4:

                continue

            options = [
                str(option).strip()
                for option in options
            ]

            if (
                any(not option for option in options)
                or
                len({option.lower() for option in options}) != 4
            ):

                continue

            try:

                answer_index = int(
                    item.get("answer_index")
                )

            except (TypeError, ValueError):

                continue

            if not 0 <= answer_index <= 3:

                continue

            cleaned["options"] = options

            cleaned["answer"] = options[answer_index]

        # ---- Mixed: keep the bloom level if it is valid ----

        if pattern == "Mixed":

            level = str(
                item.get("bloom_level", "")
            ).strip().capitalize()

            cleaned["bloom_level"] = (
                level
                if level in BLOOM_LEVEL_MARK_WEIGHT
                else ""
            )

        seen_in_batch.add(
            text.lower()
        )

        cleaned_items.append(
            cleaned
        )

    return cleaned_items, None


def collect_ai_questions(
    subject,
    course,
    unit_context,
    difficulty,
    pattern,
    number,
    seen_texts,
    section=None
):
    """
    Calls the AI (with top-up rounds) until `number` questions
    are collected. Returns (questions, error_reason).
    """

    collected = []

    for round_index in range(GEMINI_MAX_TOPUP_ROUNDS + 1):

        remaining = number - len(collected)

        if remaining <= 0:

            break

        prompt = build_ai_prompt(
            subject,
            course,
            unit_context,
            difficulty,
            pattern,
            remaining,
            [
                question["text"]
                for question in collected
            ],
            section
        )

        batch, error_reason = request_ai_question_batch(
            prompt,
            pattern
        )

        if not batch and round_index == 0:

            return [], error_reason

        for item in batch:

            key = item["text"].lower()

            if key in seen_texts:

                continue

            seen_texts.add(key)

            collected.append(item)

    return collected[:number], None


def generate_questions_with_gemini(
    subject,
    course,
    syllabus,
    number,
    total_marks,
    difficulty,
    pattern
):
    """
    Pattern-aware AI generation (function name kept so the route
    needs no other change). Returns (questions, error_reason) in
    the same shape as create_questions(). Marks are always
    computed here, never trusted from the model, so the printed
    total sums to exactly total_marks.
    """

    if not any(
        provider_available(name)
        for name in AI_PROVIDER_ORDER
    ):

        return None, "unavailable"

    unit_context = build_unit_topic_prompt_context(
        syllabus,
        subject
    )

    seen_texts = set()

    # =====================================================
    # MIXED: one AI call per section (Part A / B / C)
    # =====================================================

    if pattern == "Mixed":

        base_count, remainder = divmod(
            number,
            3
        )

        planned_counts = [
            base_count + (1 if i < remainder else 0)
            for i in range(3)
        ]

        section_items = []

        first_error = None

        for section, count in zip(
            MIXED_SECTIONS,
            planned_counts
        ):

            if count <= 0:

                section_items.append([])

                continue

            items, error_reason = collect_ai_questions(
                subject,
                course,
                unit_context,
                difficulty,
                pattern,
                count,
                seen_texts,
                section
            )

            if not items and first_error is None:

                first_error = error_reason

            for item in items:

                if not item.get("bloom_level"):

                    item["bloom_level"] = section["bloom_levels"][0]

            section_items.append(items)

        actual_counts = [
            len(items)
            for items in section_items
        ]

        if sum(actual_counts) == 0:

            return None, first_error or "other"

        section_totals = distribute_weighted_marks(
            total_marks,
            actual_counts,
            [
                section["weight"]
                for section in MIXED_SECTIONS
            ]
        )

        questions = []

        for section, items, section_total in zip(
            MIXED_SECTIONS,
            section_items,
            section_totals
        ):

            if not items:

                continue

            marks_list = distribute_marks_by_weight(
                section_total,
                [
                    BLOOM_LEVEL_MARK_WEIGHT[item["bloom_level"]]
                    for item in items
                ]
            )

            for item, marks in zip(items, marks_list):

                questions.append({

                    "text": item["text"],

                    "marks": marks,

                    "section": section["name"],

                    "bloom_level": item["bloom_level"],

                    "unit": item["unit"]

                })

        return questions, None

    # =====================================================
    # MCQ / Short / Medium / Long
    # =====================================================

    items, error_reason = collect_ai_questions(
        subject,
        course,
        unit_context,
        difficulty,
        pattern,
        number,
        seen_texts
    )

    if not items:

        return None, error_reason or "other"

    marks_distribution = compute_marks_distribution(
        total_marks,
        len(items)
    )

    questions = []

    for item, marks in zip(items, marks_distribution):

        question = {

            "text": item["text"],

            "marks": marks,

            "unit": item["unit"]

        }

        if pattern == "MCQ":

            question["options"] = item["options"]

            question["answer"] = item["answer"]

        questions.append(question)

    return questions, None
'''

ROUTE_SECTION = r'''# =========================================================
# GENERATE QUESTION PAPER
#
# Replace the WHOLE existing generate_question_paper() function
# (from the "# GENERATE QUESTION PAPER" banner down to just above
# the "# LOGOUT" banner) with this one.
#
# Requires the new AI section (build_ai_prompt / call_ai_with_fallback /
# generate_questions_with_gemini(..., pattern)) to be in app.py first.
#
# Two independent choices now:
#   generation_mode : "template" (without AI)  |  "ai"
#   pattern         : MCQ | Short | Medium | Long | Mixed
# =========================================================

VALID_PATTERNS = ("MCQ", "Short", "Medium", "Long", "Mixed")

AI_ERROR_MESSAGES = {

    "unavailable": (
        "AI generation isn't set up on this server (no API key "
        "found). Please use the 'Without AI' mode, or ask your "
        "administrator to configure it."
    ),

    "rate_limit": (
        "The AI usage limit was reached (rate limit or daily "
        "quota). Please wait a little while and try again, "
        "generate fewer questions, or use the 'Without AI' mode."
    ),

    "auth": (
        "The AI provider rejected the request - the configured "
        "API key appears to be invalid or unauthorized. Please "
        "ask your administrator to check the API key."
    ),

    "server": (
        "The AI provider had a temporary problem (or the request "
        "took too long). Please try again in a moment."
    ),

    "client": (
        "The AI provider rejected this request as invalid. Please "
        "try a different subject or syllabus, or use the "
        "'Without AI' mode."
    ),

    "other": (
        "AI question generation failed (network issue or an "
        "unexpected response). Please try again, or use the "
        "'Without AI' mode."
    )

}


@app.route(
    "/faculty/generate",
    methods=["GET", "POST"]
)
def generate_question_paper():

    if not faculty_required():

        flash(
            "Please login as faculty."
        )

        return redirect(
            url_for("faculty_login")
        )

    if request.method == "POST":

        # =================================================
        # GET FORM VALUES
        # =================================================

        subject = request.form.get(
            "subject",
            ""
        ).strip()

        course = request.form.get(
            "course",
            ""
        ).strip()

        total_marks = request.form.get(
            "total_marks",
            ""
        )

        difficulty = request.form.get(
            "difficulty",
            ""
        )

        num_questions = request.form.get(
            "num_questions",
            "20"
        )

        pattern = request.form.get(
            "pattern",
            ""
        ).strip()

        # "template" = without AI, "ai" = with AI. Anything
        # unexpected falls back to template mode.
        generation_mode = request.form.get(
            "generation_mode",
            "template"
        ).strip().lower()

        if generation_mode not in ("template", "ai"):

            generation_mode = "template"

        syllabus = request.form.get(
            "syllabus",
            ""
        ).strip()

        # =================================================
        # VALIDATION
        # =================================================

        if not subject:

            flash(
                "Please enter subject."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        if not course:

            flash(
                "Please enter course/class."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        if not total_marks:

            flash(
                "Please select total marks."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        if not difficulty:

            flash(
                "Please select difficulty."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        if not pattern:

            flash(
                "Please select question pattern."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        if pattern not in VALID_PATTERNS:

            flash(
                "Please select a valid question pattern."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        # =================================================
        # CONVERT NUMBERS
        # =================================================

        try:

            num_questions = int(
                num_questions
            )

            total_marks_int = int(
                total_marks
            )

        except ValueError:

            flash(
                "Invalid marks or number of questions."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        # =================================================
        # QUESTION LIMIT
        # =================================================

        if num_questions < 1:

            flash(
                "Number of questions must be at least 1."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        if num_questions > 100:

            flash(
                "Maximum 100 questions allowed."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        # Every question needs at least 1 mark, so Total Marks
        # can never be lower than the number of questions.
        if total_marks_int < num_questions:

            flash(
                "Total Marks must be at least equal to the number "
                "of questions, since every question needs at "
                "least 1 mark."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        # =================================================
        # LOAD LATEST UPLOADED SYLLABUS
        # =================================================

        if not syllabus:

            connection = get_connection()

            if connection:

                cursor = connection.cursor(
                    dictionary=True
                )

                try:

                    cursor.execute(
                        """
                        SELECT
                            syllabus_text
                        FROM syllabi
                        WHERE faculty_id = %s
                        AND subject = %s
                        ORDER BY uploaded_at DESC
                        LIMIT 1
                        """,
                        (
                            session["faculty_id"],
                            subject
                        )
                    )

                    latest_syllabus = cursor.fetchone()

                finally:

                    cursor.close()
                    connection.close()

                if latest_syllabus:

                    syllabus = (
                        latest_syllabus[
                            "syllabus_text"
                        ]
                        or ""
                    )

        # =================================================
        # CHECK SYLLABUS
        # =================================================

        if not syllabus.strip():

            flash(
                "No syllabus found for this subject. Please upload the syllabus first."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        # =================================================
        # CHECK TOPIC EXTRACTION QUALITY
        # (applies to BOTH modes - the AI prompt is built
        # from the same extracted units)
        # =================================================

        preview_topics = extract_topics_from_syllabus(
            syllabus,
            subject
        )

        if not topics_are_usable(
            preview_topics,
            subject
        ):

            flash(
                "Could not extract usable topics from this syllabus. "
                "Please check that it uses clear Unit/Module headings "
                "and proper punctuation, then re-upload it."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        # =================================================
        # GENERATE QUESTIONS
        #
        # generation_mode decides HOW questions are written;
        # pattern decides WHAT KIND of paper it is. Every
        # pattern works in both modes.
        # =================================================

        if generation_mode == "ai":

            questions, error_reason = generate_questions_with_gemini(
                subject=subject,
                course=course,
                syllabus=syllabus,
                number=num_questions,
                total_marks=total_marks_int,
                difficulty=difficulty,
                pattern=pattern
            )

            if not questions:

                flash(
                    AI_ERROR_MESSAGES.get(
                        error_reason,
                        AI_ERROR_MESSAGES["other"]
                    )
                )

                return redirect(
                    url_for("generate_question_paper")
                )

            if len(questions) < num_questions:

                flash(
                    f"AI returned {len(questions)} of the "
                    f"{num_questions} questions requested. You can "
                    "generate again for a full set.",
                    "success"
                )

        else:

            questions = create_questions(
                subject=subject,
                syllabus=syllabus,
                number=num_questions,
                pattern=pattern,
                difficulty=difficulty,
                total_marks=total_marks_int
            )

        if not questions:

            flash(
                "Unable to generate questions."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        # =================================================
        # SAVE QUESTION PAPER
        # =================================================

        connection = get_connection()

        if connection is None:

            flash(
                "Database connection failed."
            )

            return redirect(
                url_for("generate_question_paper")
            )

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
                    subject,
                    course,
                    total_marks_int,
                    difficulty,
                    num_questions,
                    pattern,
                    syllabus,
                    json.dumps(
                        questions
                    )
                )
            )

            connection.commit()

        except Exception as e:

            connection.rollback()

            print(
                "Question paper database error:",
                e
            )

            flash(
                "Unable to save question paper."
            )

            return redirect(
                url_for("generate_question_paper")
            )

        finally:

            cursor.close()
            connection.close()

        # =================================================
        # EXAM DURATION
        # =================================================

        if total_marks_int <= 50:

            duration = "2 Hours"

        elif total_marks_int >= 100:

            duration = "3 Hours"

        else:

            duration = "2 Hours 30 Minutes"

        # =================================================
        # DISPLAY GENERATED PAPER
        # =================================================

        return render_template(
            "question_paper.html",
            college_name="B.M.S College for Women",
            subject=subject,
            course=course,
            total_marks=total_marks_int,
            duration=duration,
            questions=questions,
            faculty_name=session.get("faculty_name", "Unknown"),
            created_at=datetime.now()
        )

    # =====================================================
    # GET REQUEST
    # =====================================================

    return render_template(
        "generate_question_paper.html"
    )
'''


def fail(message):
    print("PATCH STOPPED (nothing was changed):", message)
    sys.exit(1)


def banner_start(text, title):
    """Index of the '# ====' banner line that sits right above `# TITLE`."""
    marker = "\n# " + title + "\n"
    idx = text.find(marker)
    if idx == -1:
        return -1
    banner = text.rfind("# =====", 0, idx + 1)
    return banner


def main():
    if not os.path.exists(TARGET):
        fail(f"{TARGET} not found. Put patch_app.py next to app.py.")

    with open(TARGET, "r", encoding="utf-8") as f:
        original = f.read()

    if "def call_ai_with_fallback(" in original:
        fail("app.py already contains the new AI section (already patched).")

    text = original

    # ---------------------------------------------------------
    # 1. GEMINI_MODEL from environment variable
    # ---------------------------------------------------------
    old_model = 'GEMINI_MODEL = "gemini-flash-latest"'
    new_model = 'GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")'
    if old_model in text:
        text = text.replace(old_model, new_model, 1)

    # ---------------------------------------------------------
    # 2. Remove the old Gemini-only generation functions
    # ---------------------------------------------------------
    def cut(text, start_marker, end_marker, label):
        s = text.find(start_marker)
        if s == -1:
            fail(f"could not find {label} (start marker: {start_marker!r}).")
        e = text.find(end_marker, s)
        if e == -1:
            fail(f"could not find the end of {label} (end marker: {end_marker!r}).")
        return text[:s] + text[e:]

    text = cut(text,
               "def build_gemini_prompt(",
               "def classify_gemini_exception(",
               "build_gemini_prompt")

    text = cut(text,
               "def request_gemini_question_batch(",
               "# Maximum number of Gemini calls made for one paper",
               "request_gemini_question_batch")

    gen_banner = banner_start(text, "GENERATE QUESTION PAPER")
    if gen_banner == -1:
        fail("could not find the '# GENERATE QUESTION PAPER' banner.")

    start_fn = text.find("def generate_questions_with_gemini(")
    if start_fn == -1 or start_fn > gen_banner:
        fail("could not find generate_questions_with_gemini().")

    # remove old generate_questions_with_gemini() and insert the new AI section
    text = text[:start_fn] + AI_SECTION + "\n\n" + text[gen_banner:]

    # ---------------------------------------------------------
    # 3. Replace the generate_question_paper() route
    # ---------------------------------------------------------
    gen_banner = banner_start(text, "GENERATE QUESTION PAPER")
    logout_banner = banner_start(text, "LOGOUT")
    if gen_banner == -1 or logout_banner == -1 or logout_banner < gen_banner:
        fail("could not locate the GENERATE QUESTION PAPER / LOGOUT banners.")

    text = text[:gen_banner] + ROUTE_SECTION.strip("\n") + "\n\n\n" + text[logout_banner:]

    # ---------------------------------------------------------
    # 4. Comment tidy-up (optional)
    # ---------------------------------------------------------
    text = text.replace(
        'used only when the faculty selects the "AI" pattern.',
        'used when the faculty picks "With AI" mode (works with every pattern).'
    )

    # ---------------------------------------------------------
    # 5. Syntax check BEFORE touching the real file
    # ---------------------------------------------------------
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    try:
        py_compile.compile(tmp_path, doraise=True)
    except py_compile.PyCompileError as e:
        os.unlink(tmp_path)
        fail("the patched file did not compile, so your app.py was left untouched.\n" + str(e))
    os.unlink(tmp_path)

    shutil.copyfile(TARGET, TARGET + ".bak")
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(text)

    print("Done. app.py updated (backup saved as app.py.bak).")
    print("Next: add the 'Generation Mode' dropdown to generate_question_paper.html")
    print("and remove the 'AI' option from the pattern list.")


if __name__ == "__main__":
    main()