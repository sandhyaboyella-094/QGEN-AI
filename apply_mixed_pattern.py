"""
Applies the Exam_type.docx Mixed pattern to your app.py.

Usage (run in the folder that contains your app.py):

    python apply_mixed_pattern.py                 # reads app_grmini.py, writes app_mixed.py
    python apply_mixed_pattern.py in.py out.py

Your input file is NOT modified. Every edit must match exactly once,
otherwise the script stops and nothing is written. The patched file is
also syntax-checked before it is saved.

What it does:
  1. replaces get_mixed_pattern / get_mixed_section_counts /
     get_mixed_given_counts with the table-driven version
     (50, 60, 70, 80, 100 marks) and adds two unit helpers
  2. replaces the  if pattern == "Mixed":  block in create_questions()
  3. replaces _gemini_mixed_questions()
  4. (optional) updates the "Mixed pattern" message in the route
"""

import re
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "app_gemini.py"
DST = sys.argv[2] if len(sys.argv) > 2 else "app_mixed.py"

with open(SRC, "r", encoding="utf-8") as f:
    code = f.read()

if "MIXED_PAPER_PLANS" in code:
    sys.exit("[STOPPED] This file already contains MIXED_PAPER_PLANS. "
             "It looks like the update was applied already. Nothing was written.")


def apply(step, pattern, block, optional=False):
    """Replace exactly one match of `pattern` with `block`."""
    global code
    new_code, count = re.subn(pattern, lambda m: block, code, flags=re.DOTALL)

    if count == 0 and optional:
        print(f"[skipped] {step}: not found (optional)")
        return

    if count != 1:
        sys.exit(f"[FAILED] {step}: expected 1 match, found {count}. "
                 "Nothing was written.")

    code = new_code
    print(f"[ok] {step}")


# ---------------------------------------------------------------------
# New code
# ---------------------------------------------------------------------

PART1 = r'''# Unit codes: 1-4 = that unit of the paper, 0 = "from any unit".
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


'''

PART2 = r'''    if pattern == "Mixed":

        mixed_plan = get_mixed_pattern(total_marks)

        if not mixed_plan:
            raise ValueError(
                "Mixed pattern is supported for 50, 60, 70, 80 and 100 marks."
            )

        bloom_template_pickers = {
            bloom_level: TemplatePicker(templates)
            for bloom_level, templates in BLOOM_TEMPLATES.items()
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

                unit_index = resolve_mixed_unit_index(
                    unit_codes[added_in_section], unit_keys
                )

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

                    # Part C: a 10-mark question made of two 5-mark parts,
                    # both taken from the unit this question is assigned to.
                    subquestions = []

                    sub_bloom_levels = list(section_bloom_pair_sequence[added_in_section])
                    random.shuffle(sub_bloom_levels)

                    for sub_index in range(2):

                        sub_bloom = sub_bloom_levels[sub_index]

                        sub_topic = pick_unique_topic(
                            section_topic_picker,
                            unit_index,
                            used_topics
                        )

                        sub_template = bloom_template_pickers[sub_bloom].next_template()

                        sub_text = format_bloom_question(sub_template, sub_topic)

                        sub_key = sub_text.strip().lower()

                        retry = 0

                        while sub_key in used_questions and retry < 10:
                            retry += 1
                            sub_template = bloom_template_pickers[sub_bloom].next_template()
                            sub_topic = pick_unique_topic(
                                section_topic_picker,
                                unit_index,
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
            mixed_plan[section["name"]]["attempt"] * section["marks_per_question"]
            for section in MIXED_SECTIONS
        )

        if calculated_total != int(total_marks):
            raise ValueError(
                f"Mixed pattern total is {calculated_total}, "
                f"but {total_marks} marks were requested."
            )

        return questions
'''

PART3 = r'''def _gemini_mixed_questions(header, unit_count, total_marks):

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
          "Answers of about 150-200 words: Explain with an example, Describe with a "
          "diagram, Write a short note on, Compare, Write an algorithm or program. "
          "bloom_level is Understand, Apply or Analyze (use all three). "
          f"The unit of each question, in order, must be: {unit_order('Part B')}.\n"
        + f"Part C: exactly {plan['Part C']['given']} items, each with exactly 2 "
          "sub_questions of 5 marks each. Each sub-question is a full descriptive "
          "question: Discuss, Explain with diagram and example, Analyse, Evaluate, "
          "Design, Derive, or Write a program. bloom_level is Analyze, Evaluate or "
          "Create. Both sub-questions of an item come from the SAME unit but cover "
          "different topics, and neither depends on the other. "
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

            if name == "Part C":
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

    return questions


'''

MIXED_MESSAGE = (
    'flash(\n'
    '                "Mixed pattern is available for 50, 60, 70, 80 and 100 marks.",\n'
    '                "error"\n'
    '            )'
)

# ---------------------------------------------------------------------
# 1. Pattern table + helpers
#    (from get_mixed_pattern up to the MARKS DISTRIBUTION HELPERS banner)
# ---------------------------------------------------------------------
apply(
    "pattern table and helpers",
    r'def get_mixed_pattern\(total_marks\):.*?(?=# =+\n# MARKS DISTRIBUTION HELPERS)',
    PART1,
)

# ---------------------------------------------------------------------
# 2. Mixed block inside create_questions()
# ---------------------------------------------------------------------
apply(
    "Mixed block in create_questions()",
    r'    if pattern == "Mixed":\n\n        mixed_attempt_counts = get_mixed_section_counts\(total_marks\)\n'
    r'.*?        return questions\n(?=\n    (?:marks_distribution|fixed_marks) = )',
    PART2,
)

# ---------------------------------------------------------------------
# 3. Gemini Mixed generator
# ---------------------------------------------------------------------
apply(
    "_gemini_mixed_questions()",
    r'def _gemini_mixed_questions\(header, unit_count, total_marks\):.*?(?=def _gemini_standard_questions\()',
    PART3,
)

# ---------------------------------------------------------------------
# 4. Route message (optional)
# ---------------------------------------------------------------------
apply(
    "route message for unsupported Mixed totals",
    r'flash\(\s*"For Mixed pattern, Part A questions are "\s*"2 marks, Part B questions are 5 marks, "'
    r'\s*"and Part C questions are 10 marks\. "\s*"Choose a compatible total marks\.",\s*"error"\s*\)',
    MIXED_MESSAGE,
    optional=True,
)

# ---------------------------------------------------------------------
# Check the result before saving
# ---------------------------------------------------------------------
try:
    compile(code, DST, "exec")
except SyntaxError as e:
    sys.exit(f"[FAILED] The patched file has a syntax error at line {e.lineno}: "
             f"{e.msg}. Nothing was written.")

with open(DST, "w", encoding="utf-8") as f:
    f.write(code)

print(f"\nDone. Wrote {DST}. Review it, then replace app.py with it.")