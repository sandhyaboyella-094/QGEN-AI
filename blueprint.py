"""
blueprint.py - optional-question engine for QGEN-AI.
Drop next to app_mixed.py:   from blueprint import *
"""
import json
import re
from dataclasses import dataclass, asdict

# ------------------------------------------------------------------ model
PATTERN_MARKS = {"MCQ": 1, "Short": 2, "Medium": 5, "Long": 10}


@dataclass
class SectionSpec:
    name: str                 # "Part A"
    marks_each: int           # 2 / 5 / 10
    attempt: int              # slots the student must answer
    choice: str = "none"      # "none" | "any_n_of_m" | "either_or"
    extra: int = 0            # any_n_of_m only: M = attempt + extra
    sub_split: bool = False   # allow 5+5 split (Part C)

    @property
    def given(self):
        if self.choice == "either_or":
            return self.attempt * 2
        if self.choice == "any_n_of_m":
            return self.attempt + self.extra
        return self.attempt

    @property
    def section_marks(self):
        return self.attempt * self.marks_each


# (section name, marks each, attempt).  EDIT these to match app2.py.
MIXED_LAYOUT = {
    50:  [("Part A", 2, 5),  ("Part B", 5, 4), ("Part C", 10, 2)],
    60:  [("Part A", 2, 5),  ("Part B", 5, 6), ("Part C", 10, 2)],
    70:  [("Part A", 2, 10), ("Part B", 5, 6), ("Part C", 10, 2)],
    80:  [("Part A", 2, 10), ("Part B", 5, 6), ("Part C", 10, 3)],
    100: [("Part A", 2, 10), ("Part B", 5, 8), ("Part C", 10, 4)],
}

# choice rules keyed by marks-per-question (works for every pattern)
CHOICE_POLICY = {
    "CIA":   {2: {"choice": "none"},
              5: {"choice": "any_n_of_m", "extra": 1},
              10: {"choice": "either_or"}},
    "SEE":   {2: {"choice": "any_n_of_m", "extra": 2},
              5: {"choice": "any_n_of_m", "extra": 1},
              10: {"choice": "either_or"}},
}


# -------------------------------------------------------------- blueprint
def build_blueprint(exam_type, pattern, total_marks, required=None, optional=0):
    total_marks = int(total_marks)
    if total_marks <= 0:
        raise ValueError("Total marks must be greater than 0.")

    if pattern == "Mixed":
        if total_marks not in MIXED_LAYOUT:
            raise ValueError(f"Mixed pattern is not defined for {total_marks} marks.")
        secs = [SectionSpec(n, m, a, sub_split=(m == 10))
                for n, m, a in MIXED_LAYOUT[total_marks]]
        policy = CHOICE_POLICY.get(exam_type, {})
        for s in secs:
            rule = policy.get(s.marks_each)
            if rule:
                s.choice = rule["choice"]
                s.extra = rule.get("extra", 0)
    else:
        if pattern not in PATTERN_MARKS:
            raise ValueError(f"Unknown pattern: {pattern}")
        marks_each = PATTERN_MARKS[pattern]
        optional = int(optional or 0)
        if optional < 0:
            raise ValueError("Optional questions cannot be negative.")
        if total_marks % marks_each != 0:
            raise ValueError(f"{total_marks} marks cannot be divided into "
                             f"{marks_each}-mark questions.")
        derived = total_marks // marks_each
        required = int(required) if required not in (None, "") else derived
        if required != derived:
            raise ValueError(f"{total_marks} marks at {marks_each} marks each needs "
                             f"{derived} required questions, not {required}.")
        secs = [SectionSpec("Part A", marks_each, required,
                            "any_n_of_m" if optional else "none", optional,
                            sub_split=False)]

    validate_blueprint(secs, total_marks)
    return secs


def validate_blueprint(secs, total_marks):
    if sum(s.section_marks for s in secs) != int(total_marks):
        raise ValueError("Section marks do not add up to the total marks.")
    for s in secs:
        if s.attempt <= 0:
            raise ValueError(f"{s.name}: required questions must be > 0.")
        if s.choice == "any_n_of_m" and s.extra < 1:
            raise ValueError(f"{s.name}: 'any N of M' needs at least 1 extra question.")


def instruction(s):
    n, m, mk = s.attempt, s.given, s.marks_each
    calc = f"{n} x {mk} = {n * mk} marks"
    if s.choice == "any_n_of_m":
        return f"Answer any {n} of {m} questions ({calc})"
    if s.choice == "either_or":
        return f"Answer all questions, choosing (a) or (b) in each ({calc})"
    return f"Answer all questions ({calc})"


# ----------------------------------------------------------------- prompt
def _norm(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def build_section_prompt(s, subject, syllabus, difficulty, avoid=()):
    pair_rule = ""
    if s.choice == "either_or":
        pair_rule = ("\nQuestions are in consecutive PAIRS (1&2, 3&4, ...). The two questions in a "
                     "pair are alternatives: same unit, same marks, similar difficulty, but "
                     "testing a different concept.")
    split_rule = ""
    if s.sub_split:
        split_rule = ("\nEach question may be EITHER one direct "
                      f"{s.marks_each}-mark question OR two parts '(i) ... (ii) ...' of "
                      f"{s.marks_each // 2} marks each. Mix both styles.")
    avoid_txt = ""
    if avoid:
        avoid_txt = "\nDo NOT repeat or rephrase any of these existing questions:\n- " + "\n- ".join(avoid[-30:])

    return f"""You are a university Assistant Professor setting an examination paper.
Subject: {subject}
Difficulty: {difficulty}

Generate exactly {s.given} questions for {s.name}. Each carries {s.marks_each} marks.
The student must answer: {instruction(s)}.{pair_rule}{split_rule}

All questions must:
- be based strictly on the syllabus below
- have comparable academic difficulty
- cover different syllabus concepts (reuse a topic only with a clearly different question)
- be independently answerable
- use natural university examination wording
Do not label any question as optional.{avoid_txt}

Return ONLY JSON, no markdown: {{"questions": ["question 1", "question 2", ...]}}

SYLLABUS:
{syllabus}"""


def _parse(raw):
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    return [str(q).strip() for q in json.loads(raw)["questions"]]


def generate_section(s, call_llm, subject, syllabus, difficulty, seen, retries=2):
    """call_llm(prompt:str) -> str  (wrap your Gemini call). `seen` is a shared set."""
    avoid = []
    for _ in range(retries + 1):
        qs = _parse(call_llm(build_section_prompt(s, subject, syllabus, difficulty, avoid)))
        if len(qs) != s.given:
            avoid = avoid or []
            continue
        keys = [_norm(q) for q in qs]
        if len(set(keys)) == len(keys) and not any(k in seen for k in keys):
            seen.update(keys)
            return qs
        avoid += [q for q, k in zip(qs, keys) if k in seen or keys.count(k) > 1]
    raise ValueError(f"{s.name}: could not generate {s.given} distinct questions.")


def assemble(secs, questions_by_section):
    """-> JSON-serialisable structure for storage and rendering."""
    out, n = [], 1
    for s in secs:
        qs, items = questions_by_section[s.name], []
        if s.choice == "either_or":
            for i in range(0, len(qs), 2):
                items.append({"no": n, "a": qs[i], "b": qs[i + 1]})
                n += 1
        else:
            for q in qs:
                items.append({"no": n, "q": q})
                n += 1
        out.append({"name": s.name, "marks_each": s.marks_each, "choice": s.choice,
                    "instruction": instruction(s), "spec": asdict(s), "items": items})
    return out


def generate_paper(exam_type, pattern, total_marks, required, optional,
                   call_llm, subject, syllabus, difficulty):
    secs = build_blueprint(exam_type, pattern, total_marks, required, optional)
    seen, by_sec = set(), {}
    for s in secs:
        by_sec[s.name] = generate_section(s, call_llm, subject, syllabus, difficulty, seen)
    return secs, assemble(secs, by_sec)