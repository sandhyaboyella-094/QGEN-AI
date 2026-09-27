"""
port_mixed.py - brings the Mixed-pattern logic from app2.py into app_ai.py,
working for ANY total marks (50 / 60 / 70 / 80 / 100, or any multiple of 5
from 30 up), not only 80.

Part C: each 10-mark question is EITHER one direct 10-mark question OR
split into two 5-mark subquestions (5 + 5) - both can appear in one paper.

Works on any of these files:
  - not patched yet (full port),
  - patched with the earlier 80-marks-only version (upgrades it),
  - patched with the all-marks version where Part C was always split
    (upgrades it to direct-or-split),
  - patched with the direct-or-split version that stopped with "syllabus
    contains only N distinct usable topics" (upgrades it to reuse topics
    instead of failing).

USAGE:
    1. Put this file in the same folder as app_ai.py
    2. Run:   python port_mixed.py            (patches app_ai.py)
       or:    python port_mixed.py path\\to\\your_file.py
    3. A backup is saved next to it as <file>.bak

If any expected marker is missing, or appears more than once, the
script stops WITHOUT changing anything.
"""

import sys
import os
import shutil
import tempfile
import py_compile

TARGET = sys.argv[1] if len(sys.argv) > 1 else "app_ai.py"

MIXED_BLOCK = r'''# =========================================================
# MIXED PATTERN - WORKS FOR ANY TOTAL MARKS
# (ported from app2.py and generalised beyond 80 marks)
#
#   Part A: 2-mark questions
#   Part B: 5-mark questions
#   Part C: 10-mark questions - each one is EITHER a single
#           direct 10-mark question OR split into two 5-mark
#           subquestions (5 + 5); both kinds can appear together
#
# How many questions are ATTEMPTED in each part is chosen so the
# marks add up to exactly the selected Total Marks. The paper also
# GIVES a few extra questions in each part (Part A +2, Part B +1,
# Part C +1) so students have a choice, exactly like the 80-mark
# pattern (12 given / 10 attempt, 5 given / 4 attempt, 5 / 4).
#
# The five totals on the form use the table below - edit it if your
# college pattern differs. Any other multiple of 5 (30 and above)
# is worked out automatically by _solve_mixed_attempts().
#
# Every syllabus concept is used at most once per paper
# (a different sentence for the same topic does NOT count as a
# different topic), and each topic is classified by explicit
# lexical rules (limitations / advantages / applications /
# types / algorithm / model / library / data / preprocessing /
# concept) so the question stem fits it.
# =========================================================

PART_HEADING_CSS = """
.part-heading {
    text-align: center;
    font-weight: bold;
    font-style: italic;
    text-decoration: underline;
    margin: 18px 0 12px 0;
}
"""


def format_part_heading(part_label):
    """Return a centered, bold, italic and underlined Part A/B/C heading."""
    safe_label = str(part_label).strip()
    return (
        '<div class="part-heading">'
        f'<strong><em><u>{safe_label}</u></em></strong>'
        '</div>'
    )


# -----------------------------
# Topic classification keywords
# -----------------------------
TOPIC_TYPE_RULES = [
    ("limitations", ("limitations of", "limit of", "limits of", "disadvantages of", "drawbacks of")),
    ("advantages", ("advantages of", "benefits of", "merits of")),
    ("applications", ("applications of", "application of", "uses of", "use of")),
    ("types", ("types of", "type of", "kinds of")),
    ("preprocessing", ("preprocessing", "pre-processing", "data preparation", "data cleaning")),
    ("library", ("libraries", "library", "tools", "toolkit", "packages")),
    ("data", ("real data", "data set", "dataset", "data preparation", "data preprocessing", "working with data")),
    ("algorithm", ("algorithm", "classifier", "clustering", "regression", "neighbour", "neighbor", "k-means", "dbscan", "naive bayes")),
    ("model", ("models", "model", "linear models", "machine learning system")),
]

QUESTION_TEMPLATES = {
    "concept": {
        2: [
            "Define {topic}.",
            "What is {topic}?",
            "State the purpose of {topic}.",
            "State one important characteristic of {topic}.",
        ],
        5: [
            "Explain {topic} and describe its main characteristics.",
            "Describe {topic} and explain its importance in machine learning.",
            "Explain the working of {topic} with a suitable example.",
        ],
        "higher": [
            "Analyze {topic} and explain the factors that affect its use.",
            "Explain how {topic} can be applied in a practical machine learning task.",
            "Discuss the advantages and limitations of {topic} in practice.",
        ],
    },
    "algorithm": {
        2: [
            "Define {topic}.",
            "What is {topic}?",
            "State the main purpose of {topic}.",
            "State one important characteristic of {topic}.",
        ],
        5: [
            "Explain the working of {topic} with a suitable example.",
            "Describe the main steps involved in {topic}.",
            "Explain how {topic} is used in machine learning.",
        ],
        "higher": [
            "Analyze the working of {topic} and explain the factors that affect its performance.",
            "Apply {topic} to a suitable machine learning problem and explain the procedure.",
            "Compare the use of {topic} in two suitable machine learning situations.",
        ],
    },
    "model": {
        2: [
            "Define {topic}.",
            "What is {topic}?",
            "State the purpose of {topic}.",
            "State one characteristic of {topic}.",
        ],
        5: [
            "Explain {topic} with a suitable example.",
            "Describe the main features of {topic}.",
            "Explain how {topic} is used for prediction or classification.",
        ],
        "higher": [
            "Analyze {topic} and discuss the situations in which it is suitable.",
            "Apply {topic} to a suitable problem and explain the expected outcome.",
            "Compare {topic} with a related model and state the major differences.",
        ],
    },
    "library": {
        2: [
            "What is meant by {topic}?",
            "State the purpose of {topic}.",
            "Name one important use of {topic}.",
            "State one feature of {topic}.",
        ],
        5: [
            "Explain {topic} and state its role in machine learning.",
            "Describe the main uses of {topic} with suitable examples.",
            "Explain how {topic} supports machine learning tasks.",
        ],
        "higher": [
            "Analyze the role of {topic} in a machine learning workflow.",
            "Explain how {topic} can be selected and used for a practical machine learning task.",
            "Compare the roles of the major components included in {topic}.",
        ],
    },
    "data": {
        2: [
            "What is meant by {topic}?",
            "State the purpose of {topic}.",
            "State any two important characteristics of {topic}.",
            "Why is {topic} important in machine learning?",
        ],
        5: [
            "Explain {topic} and describe the main steps involved.",
            "Describe the importance of {topic} in preparing data for machine learning.",
            "Explain {topic} with a suitable example.",
        ],
        "higher": [
            "Analyze the role of {topic} in a machine learning workflow.",
            "Apply {topic} to a suitable data-processing situation and explain the result.",
            "Explain the problems that may occur when {topic} is not handled properly.",
        ],
    },
    "preprocessing": {
        2: [
            "What is meant by {topic}?",
            "State the purpose of {topic}.",
            "State one benefit of {topic}.",
            "Why is {topic} used before model training?",
        ],
        5: [
            "Explain {topic} and describe how it is carried out.",
            "Describe the role of {topic} in data preprocessing.",
            "Explain {topic} with a suitable example.",
        ],
        "higher": [
            "Analyze how {topic} affects the quality of a machine learning model.",
            "Apply {topic} to a suitable preprocessing task and explain the steps.",
            "Compare the results obtained with and without {topic}.",
        ],
    },
    "limitations": {
        2: [
            "State any two limitations of {base_topic}.",
            "List any two limitations of {base_topic}.",
        ],
        5: [
            "Explain the major limitations of {base_topic}.",
            "Discuss the limitations of {base_topic} and their effect on its use.",
        ],
        "higher": [
            "Analyze the limitations of {base_topic} and explain how they affect its practical use.",
            "Evaluate the situations in which the limitations of {base_topic} become important.",
        ],
    },
    "advantages": {
        2: [
            "State any two advantages of {base_topic}.",
            "List any two advantages of {base_topic}.",
        ],
        5: [
            "Explain the major advantages of {base_topic}.",
            "Discuss the advantages of {base_topic} with suitable points.",
        ],
        "higher": [
            "Analyze the advantages of {base_topic} and explain when they are useful.",
            "Evaluate the practical benefits of {base_topic} for a suitable application.",
        ],
    },
    "applications": {
        2: [
            "State any two applications of {base_topic}.",
            "List any two uses of {base_topic}.",
        ],
        5: [
            "Explain the important applications of {base_topic}.",
            "Describe five important uses of {base_topic} with brief explanations.",
        ],
        "higher": [
            "Analyze how {base_topic} can be applied to a suitable real-world problem.",
            "Explain how {base_topic} can be selected for different practical applications.",
        ],
    },
    "types": {
        2: [
            "Name any two types of {base_topic}.",
            "List any two types of {base_topic}.",
        ],
        5: [
            "Explain the main types of {base_topic}.",
            "Describe the important types of {base_topic} with suitable examples.",
        ],
        "higher": [
            "Compare the major types of {base_topic} and state where each is suitable.",
            "Analyze the differences among the major types of {base_topic}.",
        ],
    },
}

# Safe generic stems. They are used only when a topic does not match a
# specialized lexical category; they never invent names, people or facts.
SAFE_GENERIC_TEMPLATES = {
    2: ["Define {topic}.", "What is {topic}?", "State the purpose of {topic}."],
    5: ["Explain {topic} with a suitable example.", "Describe {topic} and its main characteristics."],
    "higher": ["Analyze {topic} and explain its importance in machine learning.", "Apply {topic} to a suitable problem and explain the procedure."],
}


def normalize_topic(topic):
    topic = re.sub(r"\s+", " ", str(topic or "")).strip(" .:;-\t\r\n")
    topic = re.sub(r"^(introduction to|overview of|basics of|fundamentals of)\s+", "", topic, flags=re.I)
    return topic.strip()


def detect_topic_type(topic):
    """Classify a syllabus phrase using explicit lexical rules only."""
    text = normalize_topic(topic).lower()
    for kind, phrases in TOPIC_TYPE_RULES:
        if any(phrase in text for phrase in phrases):
            return kind
    return "concept"


def base_topic_for_type(topic, topic_type):
    text = normalize_topic(topic)
    patterns = {
        "limitations": r"^(?:the\s+)?(?:limitations?|limits?|disadvantages?|drawbacks?)\s+of\s+",
        "advantages": r"^(?:the\s+)?(?:advantages?|benefits?|merits?)\s+of\s+",
        "applications": r"^(?:the\s+)?(?:applications?|uses?)\s+of\s+",
        "types": r"^(?:the\s+)?(?:types?|kinds?)\s+of\s+",
    }
    pattern = patterns.get(topic_type)
    if pattern:
        stripped = re.sub(pattern, "", text, flags=re.I).strip()
        return stripped or text
    return text


def template_for(topic, marks, level="normal", template_index=0):
    topic = normalize_topic(topic)
    topic_type = detect_topic_type(topic)
    base_topic = base_topic_for_type(topic, topic_type)

    if level == "higher":
        bank = QUESTION_TEMPLATES.get(topic_type, SAFE_GENERIC_TEMPLATES).get("higher", SAFE_GENERIC_TEMPLATES["higher"])
    else:
        bank = QUESTION_TEMPLATES.get(topic_type, SAFE_GENERIC_TEMPLATES).get(marks, SAFE_GENERIC_TEMPLATES[marks])

    template = bank[template_index % len(bank)]
    return template.format(topic=topic, base_topic=base_topic)


def question_is_valid(text, marks):
    """Keep 2-mark questions short and low-cognitive-load."""
    text_lower = text.lower()
    if int(marks) == 2:
        forbidden = (
            "analyze", "evaluate", "design", "develop", "devise", "construct",
            "justify", "defend", "critique", "compare", "calculate", "solve",
            "demonstrate", "implement", "step by step", "critically"
        )
        if any(word in text_lower for word in forbidden):
            return False
        if len(text.split()) > 16:
            return False
    return True


def build_subquestion_marks(total_marks):
    total_marks = int(total_marks)
    if total_marks == 10:
        return [5, 5]
    raise ValueError("Only 10-mark questions are split into 5+5 subquestions in Mixed pattern.")


# Questions ATTEMPTED per part: (Part A x 2 marks, Part B x 5 marks,
# Part C x 10 marks). 2a + 5b + 10c must equal the total.
MIXED_PATTERN_TABLE = {
    50: (5, 4, 2),      # 10 + 20 + 20
    60: (5, 4, 3),      # 10 + 20 + 30
    70: (10, 4, 3),     # 20 + 20 + 30
    80: (10, 4, 4),     # 20 + 20 + 40
    100: (10, 6, 5),    # 20 + 30 + 50
}

# Extra questions GIVEN (for choice) on top of those attempted.
MIXED_EXTRA_GIVEN = (2, 1, 1)


def _solve_mixed_attempts(total_marks):
    """
    Works out (a, b, c) for any multiple-of-5 total (30 or more) so that
    2a + 5b + 10c == total_marks, keeping roughly the 80-mark proportions
    (about 25% Part A, 25% Part B, 50% Part C). Returns None if the total
    cannot be split this way.
    """
    total = int(total_marks)

    if total < 30 or total % 5:
        return None

    ideal_c = max(1, int(total * 0.5 / 10 + 0.5))
    ideal_a = max(5, int(total * 0.25 / 2 / 5 + 0.5) * 5)

    for c in range(ideal_c, 0, -1):
        # nearest to the ideal Part A size first, then smaller, then larger
        a_candidates = list(range(ideal_a, 4, -5)) + list(range(ideal_a + 5, total // 2 + 1, 5))
        for a in a_candidates:
            remaining = total - 10 * c - 2 * a
            if remaining >= 5 and remaining % 5 == 0:
                return a, remaining // 5, c

    return None


def get_mixed_pattern(total_marks):
    """
    Returns {"Part A": {"attempt": n, "given": n}, ...} for the total, or
    None when the total is not supported (not a multiple of 5, or < 30).
    """
    total = int(total_marks)

    attempts = MIXED_PATTERN_TABLE.get(total) or _solve_mixed_attempts(total)

    if not attempts:
        return None

    return {
        name: {"attempt": attempt, "given": attempt + extra}
        for name, attempt, extra in zip(
            ("Part A", "Part B", "Part C"),
            attempts,
            MIXED_EXTRA_GIVEN
        )
    }


def get_mixed_section_counts(total_marks):
    pattern = get_mixed_pattern(total_marks)
    return {name: value["attempt"] for name, value in pattern.items()} if pattern else None


def get_mixed_given_counts(total_marks):
    pattern = get_mixed_pattern(total_marks)
    return {name: value["given"] for name, value in pattern.items()} if pattern else None


def _topic_pools(syllabus, subject):
    topics_by_unit = _extract_topics_by_unit(syllabus, subject)
    if not topics_by_unit:
        return {}
    return topics_by_unit


def build_topic_question(topic, marks, level="normal", seed=0):
    # Try several templates so a validation failure never inserts a bad
    # 2-mark question.
    for offset in range(10):
        text = template_for(topic, marks, level, seed + offset)
        if question_is_valid(text, marks):
            return text
    return None


def normalize_topic_for_comparison(topic):
    """Create a stable topic key for duplicate/near-duplicate detection."""
    text = normalize_topic(topic).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)

    # These words describe a heading rather than the actual syllabus concept.
    # Removing them prevents phrases such as "Working with Real Data" and
    # "Real Data" from being treated as two different topics.
    heading_words = {
        "introduction", "overview", "basics", "basic", "fundamentals",
        "working", "using", "use", "some", "sample", "essential",
        "main", "important", "general", "related", "study", "of",
        "the", "and", "with", "for", "in", "to", "on", "an", "a",
        "machine", "learning"
    }

    tokens = []
    for token in text.split():
        # Simple singular normalization: networks -> network, models -> model.
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        if token not in heading_words:
            tokens.append(token)

    return " ".join(sorted(set(tokens)))


def topics_are_same_topic(topic_a, topic_b):
    """Return True when two syllabus phrases represent the same concept."""
    key_a = normalize_topic_for_comparison(topic_a)
    key_b = normalize_topic_for_comparison(topic_b)

    if not key_a or not key_b:
        return normalize_topic(topic_a).lower() == normalize_topic(topic_b).lower()

    if key_a == key_b:
        return True

    a = set(key_a.split())
    b = set(key_b.split())
    common = a & b

    # "K-Means" vs "K-Means clustering", "neural network" vs
    # "neural networks", etc. should count as the same topic.
    smaller = min(len(a), len(b))
    if smaller >= 1 and len(common) == smaller:
        return True

    # Avoid treating unrelated topics as duplicates just because they share
    # one generic word. A high overlap is required for near-duplicate phrases.
    similarity = len(common) / len(a | b)
    return similarity >= 0.70


def topic_already_used(topic, used_topic_names):
    return any(topics_are_same_topic(topic, old) for old in used_topic_names)


def flatten_unique_topics(topics_by_unit):
    """Return (unit, topic) pairs with semantic/topic-level duplicates removed."""
    unique = []
    for unit, topics in topics_by_unit.items():
        for topic in topics:
            topic = normalize_topic(topic)
            if not topic:
                continue
            if topic_already_used(topic, [item[1] for item in unique]):
                continue
            unique.append((unit, topic))
    return unique


def select_unique_topic_pool(topics_by_unit, required_count):
    """
    Select unique syllabus topics first, rather than selecting a topic and then
    merely changing its question sentence. Units are balanced as evenly as
    possible. No topic is returned twice unless the syllabus contains fewer
    unique topics than required_count.
    """
    all_unique = flatten_unique_topics(topics_by_unit)
    if not all_unique:
        return []

    by_unit = {}
    for unit, topic in all_unique:
        by_unit.setdefault(unit, []).append(topic)

    units = list(by_unit.keys())
    random.shuffle(units)

    selected = []
    used = []

    while len(selected) < min(required_count, len(all_unique)):
        progress = False
        random.shuffle(units)
        for unit in units:
            if not by_unit[unit]:
                continue
            topic = by_unit[unit].pop(random.randrange(len(by_unit[unit])))
            if topic_already_used(topic, used):
                continue
            selected.append((unit, topic))
            used.append(topic)
            progress = True
            if len(selected) >= min(required_count, len(all_unique)):
                break
        if not progress:
            break

    return selected


def question_fingerprint(text):
    """Normalize question text for exact duplicate detection."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()


# Share of Part C questions that are split into two 5-mark subquestions
# (5 + 5). The rest are ONE direct 10-mark question. When a paper has 2 or
# more Part C questions, at least one of each kind is always included, so
# both formats appear. Set to 1.0 for always-split, or 0.0 for always-direct
# (a value of 0.0 / 1.0 turns the "at least one of each" rule off).
MIXED_C_SPLIT_SHARE = 0.5


def _plan_part_c(count):
    """
    Decides, for each Part C question, whether it is split into two
    5-mark subquestions (True) or is a single direct 10-mark question
    (False). Returns a list of length `count`, in paper order.
    """
    if count <= 0:
        return []

    plan = [random.random() < MIXED_C_SPLIT_SHARE for _ in range(count)]

    if 0.0 < MIXED_C_SPLIT_SHARE < 1.0 and count >= 2:
        if all(plan):
            plan[random.randrange(count)] = False
        elif not any(plan):
            plan[random.randrange(count)] = True

    return plan


def _extend_topic_pool(topic_pool, required):
    """
    When the syllabus has fewer distinct topics than the paper has
    question slots, cycle back through the topics (reshuffled on every
    pass) instead of refusing to generate. Topics are only reused when
    there is no alternative, and never back to back.
    """
    extended = list(topic_pool)

    if not extended or len(extended) >= required:
        return extended

    while len(extended) < required:
        refill = list(topic_pool)
        random.shuffle(refill)

        if len(refill) > 1 and refill[0][1] == extended[-1][1]:
            refill.append(refill.pop(0))

        extended.extend(refill)

    return extended[:required]


def _fresh_question(topic, marks, level, seed, used_fingerprints):
    """
    A valid question for `topic` whose text has not been used yet in this
    paper. Tries every stem in the topic's template bank, starting at
    `seed`, so a topic that is used twice is asked in two different ways.
    Returns None when every stem for this topic is already used.
    """
    for offset in range(40):
        text = template_for(topic, marks, level, seed + offset)

        if (
            question_is_valid(text, marks)
            and question_fingerprint(text) not in used_fingerprints
        ):
            return text

    return None


def _pick_fresh_question(
    topic_pool,
    index,
    marks,
    level,
    seed,
    used_fingerprints,
    avoid_topic=None
):
    """
    Takes the topic at topic_pool[index] and writes a question for it. If
    that topic cannot produce a new question (all its stems are used), it
    swaps in a later topic that can. `avoid_topic` (used for the second
    half of a split Part C question) is skipped unless nothing else works.
    Returns (unit, topic, text) or None.
    """
    passes = [True, False] if avoid_topic else [False]

    for skip_avoided in passes:
        for j in range(index, len(topic_pool)):
            unit, topic = topic_pool[j]

            if skip_avoided and avoid_topic and topics_are_same_topic(topic, avoid_topic):
                continue

            text = _fresh_question(topic, marks, level, seed, used_fingerprints)

            if text:
                topic_pool[index], topic_pool[j] = topic_pool[j], topic_pool[index]
                return unit, topic, text

    return None


def _too_few_topics_error(distinct_topics, total_marks):
    return ValueError(
        f"The syllabus has only {distinct_topics} distinct usable topics, which is "
        f"not enough to write a {int(total_marks)}-mark Mixed paper without repeating "
        "the same question. Please upload a more detailed syllabus."
    )


def create_mixed_questions(subject, syllabus, total_marks):
    """
    Mixed pattern for any supported Total Marks: Part A (2 marks),
    Part B (5 marks), Part C (10 marks). A Part C question is EITHER one
    direct 10-mark question OR a 10-mark question split into two 5-mark
    subquestions (5 + 5) - both kinds can appear in the same paper.
    For 80 marks that is 12 x 2, 5 x 5, 5 x 10 given (10 / 4 / 4 attempted).

    Every syllabus concept is used once per paper whenever the syllabus
    has enough distinct topics. If it has fewer than the paper needs, the
    topics are reused (each time with different wording) instead of
    refusing to generate. Raises ValueError only when the syllabus has no
    usable topics at all, or too few to avoid repeating a question.
    """
    topics_by_unit = _topic_pools(syllabus, subject)
    if not topics_by_unit:
        return []

    mixed_given = get_mixed_given_counts(total_marks)
    if not mixed_given:
        raise ValueError(
            "Mixed pattern needs a Total Marks value that is a multiple of 5 "
            "(30 or more), for example 50, 60, 70, 80 or 100."
        )

    questions = []
    used_question_fingerprints = set()

    # Decide up front which Part C questions are split (5+5) and which are
    # direct 10-mark questions, so we know how many topic slots we need:
    # one per Part-A question, one per Part-B question, and one (direct) or
    # two (split) per Part-C question.
    part_c_plan = _plan_part_c(mixed_given["Part C"])

    required_topics = (
        mixed_given["Part A"]
        + mixed_given["Part B"]
        + sum(2 if is_split else 1 for is_split in part_c_plan)
    )

    unique_pool = select_unique_topic_pool(topics_by_unit, required_topics)

    if not unique_pool:
        raise ValueError(
            "Could not extract any usable topics from this syllabus. "
            "Please check that it uses clear Unit/Module headings and proper punctuation."
        )

    distinct_topics = len(unique_pool)

    topic_pool = _extend_topic_pool(unique_pool, required_topics)

    topic_index = 0

    # Part A: 2 marks each.
    for i in range(mixed_given["Part A"]):
        picked = _pick_fresh_question(
            topic_pool, topic_index, 2, "normal", i, used_question_fingerprints
        )
        if not picked:
            raise _too_few_topics_error(distinct_topics, total_marks)
        unit, topic, q = picked
        topic_index += 1
        used_question_fingerprints.add(question_fingerprint(q))
        questions.append({
            "text": q,
            "marks": 2,
            "section": "Part A",
            "bloom_level": "Remember/Understand",
            "unit": f"Unit {unit + 1}",
            "subquestions": [],
            "topics": [topic],
        })

    # Part B: 5 marks each.
    for i in range(mixed_given["Part B"]):
        picked = _pick_fresh_question(
            topic_pool, topic_index, 5, "normal", i + 10, used_question_fingerprints
        )
        if not picked:
            raise _too_few_topics_error(distinct_topics, total_marks)
        unit, topic, q = picked
        topic_index += 1
        used_question_fingerprints.add(question_fingerprint(q))
        questions.append({
            "text": q,
            "marks": 5,
            "section": "Part B",
            "bloom_level": "Understand/Apply/Analyze",
            "unit": f"Unit {unit + 1}",
            "subquestions": [],
            "topics": [topic],
        })

    # Part C: 10 marks each. Either ONE direct 10-mark question, or a
    # 10-mark question containing two 5-mark subquestions (the two
    # subquestions use different topics whenever the syllabus allows).
    for i, is_split in enumerate(part_c_plan):

        if not is_split:
            picked = _pick_fresh_question(
                topic_pool, topic_index, 10, "higher", i + 30, used_question_fingerprints
            )
            if not picked:
                raise _too_few_topics_error(distinct_topics, total_marks)
            unit, topic, q = picked
            topic_index += 1
            used_question_fingerprints.add(question_fingerprint(q))
            questions.append({
                "text": q,
                "marks": 10,
                "section": "Part C",
                "bloom_level": "Analyze/Evaluate/Create",
                "unit": f"Unit {unit + 1}",
                "subquestions": [],
                "topics": [topic],
            })
            continue

        picked_a = _pick_fresh_question(
            topic_pool, topic_index, 5, "higher", i * 2 + 20, used_question_fingerprints
        )
        if not picked_a:
            raise _too_few_topics_error(distinct_topics, total_marks)
        unit_a, topic_a, a = picked_a
        topic_index += 1
        used_question_fingerprints.add(question_fingerprint(a))

        picked_b = _pick_fresh_question(
            topic_pool, topic_index, 5, "higher", i * 2 + 21,
            used_question_fingerprints, avoid_topic=topic_a
        )
        if not picked_b:
            raise _too_few_topics_error(distinct_topics, total_marks)
        unit_b, topic_b, b = picked_b
        topic_index += 1
        used_question_fingerprints.add(question_fingerprint(b))

        questions.append({
            "text": "",
            "marks": 10,
            "section": "Part C",
            "bloom_level": "Analyze/Evaluate/Create",
            "unit": f"Unit {unit_a + 1}",
            "subquestions": [
                {
                    "text": a,
                    "marks": 5,
                    "bloom_level": "Analyze/Apply",
                    "unit": f"Unit {unit_a + 1}",
                    "topics": [topic_a],
                },
                {
                    "text": b,
                    "marks": 5,
                    "bloom_level": "Evaluate/Create",
                    "unit": f"Unit {unit_b + 1}",
                    "topics": [topic_b],
                },
            ],
            "topics": [topic_a, topic_b],
        })

    return questions
'''


def fail(message):
    print("PATCH STOPPED (nothing was changed):", message)
    sys.exit(1)


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        fail(f"expected exactly 1 match for {label}, found {count}.")
    return text.replace(old, new, 1)


def write_result(original, text):
    # ---------------------------------------------------------
    # Syntax check BEFORE touching the real file
    # ---------------------------------------------------------
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    try:
        py_compile.compile(tmp_path, doraise=True)
    except py_compile.PyCompileError as exc:
        os.unlink(tmp_path)
        fail("the patched file did not compile, so your file was left untouched.\n" + str(exc))
    os.unlink(tmp_path)

    shutil.copyfile(TARGET, TARGET + ".bak")
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"Done. {TARGET} updated (backup saved as {TARGET}.bak).")


def upgrade_part_c(text):
    """Replace the Part C / topic-picking code with the latest version."""
    marker = "# Share of Part C questions"
    fallback = "def create_mixed_questions("

    if text.count(fallback) != 1:
        fail("could not find create_mixed_questions() exactly once.")

    # newer files already have the Part C constant + helpers above the
    # function; start replacing from there so nothing is defined twice
    start = text.find(marker)
    if start == -1 or start > text.find(fallback):
        start = text.find(fallback)

    title = "\n# QUESTION GENERATION\n"
    if text.count(title) != 1:
        fail("could not find the '# QUESTION GENERATION' banner exactly once.")
    banner = text.rfind("# =====", 0, text.find(title) + 1)
    if banner == -1 or banner < start:
        fail("could not locate the end of create_mixed_questions().")

    new_code = MIXED_BLOCK[MIXED_BLOCK.index(marker):]
    return text[:start] + new_code.rstrip("\n") + "\n\n\n" + text[banner:]


def upgrade_to_all_marks(text):
    """Upgrade a file that already has the 80-marks-only Mixed port."""

    # new pattern table + solver + get_mixed_pattern()
    start = "def get_mixed_pattern(total_marks):"
    end = "def get_mixed_section_counts(total_marks):"
    if text.count(start) != 1 or text.count(end) != 1:
        fail("could not find get_mixed_pattern() / get_mixed_section_counts().")
    s = text.find(start)
    e = text.find(end)
    if e < s:
        fail("get_mixed_pattern() and get_mixed_section_counts() are out of order.")
    new_pattern_code = MIXED_BLOCK[
        MIXED_BLOCK.index("# Questions ATTEMPTED per part"):
        MIXED_BLOCK.index(end)
    ]
    text = text[:s] + new_pattern_code + text[e:]

    # messages inside create_mixed_questions()
    text = replace_once(
        text,
        'raise ValueError("Mixed pattern currently supports exactly 80 marks.")',
        'raise ValueError(\n            "Mixed pattern needs a Total Marks value that is a multiple of 5 "\n'
        '            "(30 or more), for example 50, 60, 70, 80 or 100."\n        )',
        "the 80-marks-only error in create_mixed_questions()"
    )
    text = replace_once(
        text,
        'f"but the 80-mark Mixed paper requires {required_topics}. "',
        'f"but a {int(total_marks)}-mark Mixed paper requires {required_topics}. "',
        "the topic-count error message"
    )

    # route message
    old_route_msg = (
        '                    "Mixed pattern uses the 80-mark structure: "\n'
        '                    "Part A = 12 questions of 2 marks, Part B = 5 questions of 5 marks, "\n'
        '                    "and Part C = 5 questions of 10 marks (5+5). "\n'
        '                    "Please select 80 total marks.",\n'
    )
    new_route_msg = (
        '                    "Mixed pattern needs a Total Marks value that is a multiple of 5 "\n'
        '                    "(30 or more), for example 50, 60, 70, 80 or 100.",\n'
    )
    text = replace_once(text, old_route_msg, new_route_msg, "the route's 80-marks-only message")
    return text


def main():
    if not os.path.exists(TARGET):
        fail(f"{TARGET} not found. Put port_mixed.py next to app_ai.py.")

    with open(TARGET, "r", encoding="utf-8") as f:
        original = f.read()

    if "def create_mixed_questions(" in original:
        text = original
        changed = False
        if "MIXED_PATTERN_TABLE" not in text:
            text = upgrade_to_all_marks(text)
            changed = True
        if "_pick_fresh_question" not in text:
            text = upgrade_part_c(text)
            changed = True
        if not changed:
            fail("this file already has the latest Mixed logic (all marks, direct or split Part C, topic reuse).")
        write_result(original, text)
        return

    text = original

    # ---------------------------------------------------------
    # 1. MIXED_SECTIONS: add marks_per_question (2 / 5 / 10)
    #    (keeps weight / bloom_levels / overlap_levels as they are)
    # ---------------------------------------------------------
    for part, marks in (("Part A", 2), ("Part B", 5), ("Part C", 10)):
        old = f'"name": "{part}",'
        new = f'"name": "{part}",\n\n        "marks_per_question": {marks},'
        text = replace_once(text, old, new, f'MIXED_SECTIONS entry for {part}')

    # ---------------------------------------------------------
    # 2. Insert the ported helpers + create_mixed_questions()
    #    just above the "# QUESTION GENERATION" banner
    # ---------------------------------------------------------
    title = "\n# QUESTION GENERATION\n"
    if text.count(title) != 1:
        fail("could not find the '# QUESTION GENERATION' banner exactly once.")
    idx = text.find(title)
    banner = text.rfind("# =====", 0, idx + 1)
    if banner == -1:
        fail("could not locate the banner line above '# QUESTION GENERATION'.")
    text = text[:banner] + MIXED_BLOCK.strip("\n") + "\n\n\n" + text[banner:]

    # ---------------------------------------------------------
    # 3. Replace the old Mixed branch inside create_questions()
    # ---------------------------------------------------------
    start_marker = '    if pattern == "Mixed":\n\n        base_count = number // 3'
    end_marker = (
        "        return questions\n\n"
        "    # =====================================================\n"
        "    # GENERATE QUESTIONS (MCQ / Short / Medium / Long)"
    )
    if text.count(start_marker) != 1:
        fail("could not find the old Mixed branch in create_questions().")
    s = text.find(start_marker)
    e = text.find(end_marker, s)
    if e == -1:
        fail("could not find the end of the old Mixed branch.")
    keep_return = "        return questions"
    new_branch = (
        '    if pattern == "Mixed":\n\n'
        "        # Part A (2 marks), Part B (5 marks), Part C (10 = 5+5)\n"
        "        # for any supported total - see create_mixed_questions().\n"
        "        return create_mixed_questions(\n"
        "            subject,\n"
        "            syllabus,\n"
        "            total_marks\n"
        "        )"
    )
    text = text[:s] + new_branch + text[e + len(keep_return):]

    # ---------------------------------------------------------
    # 4. Route: Mixed handling before the QUESTION LIMIT checks
    # ---------------------------------------------------------
    limit_marker = (
        "        # =================================================\n"
        "        # QUESTION LIMIT\n"
    )
    mixed_route = (
        "        # =================================================\n"
        "        # MIXED PATTERN - works for any supported total marks\n"
        "        # Part A: 2-mark, Part B: 5-mark, Part C: 10-mark (5+5)\n"
        "        # (e.g. 80 marks: 12/5/5 given, 10/4/4 attempted)\n"
        "        # =================================================\n\n"
        '        if pattern == "Mixed":\n\n'
        "            mixed_counts = get_mixed_section_counts(total_marks_int)\n\n"
        "            if mixed_counts is None:\n\n"
        "                flash(\n"
        '                    "Mixed pattern needs a Total Marks value that is a multiple of 5 "\n'
        '                    "(30 or more), for example 50, 60, 70, 80 or 100.",\n'
        '                    "error"\n'
        "                )\n\n"
        "                return redirect(\n"
        '                    url_for("generate_question_paper")\n'
        "                )\n\n"
        "            # The Mixed pattern has a fixed number of printed\n"
        "            # questions for each total (e.g. 80 marks: 12+5+5 = 22),\n"
        "            # so the form's num_questions value is not used for it.\n"
        "            num_questions = sum(\n"
        "                get_mixed_given_counts(total_marks_int).values()\n"
        "            )\n\n"
    )
    text = replace_once(text, limit_marker, mixed_route + limit_marker, "the QUESTION LIMIT banner in the route")

    # ---------------------------------------------------------
    # 5. Route: catch generation errors (too few topics etc.)
    # ---------------------------------------------------------
    old_call = (
        "            questions = create_questions(\n"
        "                subject=subject,\n"
        "                syllabus=syllabus,\n"
        "                number=num_questions,\n"
        "                pattern=pattern,\n"
        "                difficulty=difficulty,\n"
        "                total_marks=total_marks_int\n"
        "            )\n"
    )
    new_call = (
        "            try:\n\n"
        "                questions = create_questions(\n"
        "                    subject=subject,\n"
        "                    syllabus=syllabus,\n"
        "                    number=num_questions,\n"
        "                    pattern=pattern,\n"
        "                    difficulty=difficulty,\n"
        "                    total_marks=total_marks_int\n"
        "                )\n\n"
        "            except ValueError as e:\n\n"
        '                print("QUESTION GENERATION ERROR:", e)\n\n'
        '                flash(str(e), "error")\n\n'
        "                return redirect(\n"
        '                    url_for("generate_question_paper")\n'
        "                )\n\n"
        "            except Exception as e:\n\n"
        '                print("UNEXPECTED QUESTION GENERATION ERROR:", repr(e))\n\n'
        "                flash(\n"
        '                    "Question paper generation failed. Please check the Flask terminal for details.",\n'
        '                    "error"\n'
        "                )\n\n"
        "                return redirect(\n"
        '                    url_for("generate_question_paper")\n'
        "                )\n"
    )
    text = replace_once(text, old_call, new_call, "the create_questions() call in the route")

    # ---------------------------------------------------------
    # 6. Route: pass the Mixed structure to question_paper.html
    # ---------------------------------------------------------
    old_render = (
        '            created_at=datetime.now()\n'
        "        )"
    )
    new_render = (
        '            created_at=datetime.now(),\n'
        "            mixed_counts=(\n"
        '                get_mixed_section_counts(total_marks_int) if pattern == "Mixed" else {}\n'
        "            ),\n"
        "            mixed_given_counts=(\n"
        '                get_mixed_given_counts(total_marks_int) if pattern == "Mixed" else {}\n'
        "            ),\n"
        '            mixed_sections=(MIXED_SECTIONS if pattern == "Mixed" else [])\n'
        "        )"
    )
    text = replace_once(text, old_render, new_render, "the render_template(question_paper.html) call")

    write_result(original, text)


if __name__ == "__main__":
    main()