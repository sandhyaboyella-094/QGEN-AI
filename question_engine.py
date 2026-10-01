"""
question_engine.py - subject-independent question formation for QGEN-AI.

Takes {unit_index: [topics]} extracted from ANY syllabus and builds questions.
No subject-specific rules: templates are chosen by marks (2/5/10) and by the
grammatical/semantic TYPE of the topic (concept, process, structure, attack, math).
"""
import random
import re

# ---------------------------------------------------------------- cleaning
VAGUE_TOPICS = {
    "services", "mechanisms", "foundations", "mathematical foundations",
    "attacks", "principles", "concepts", "basics", "techniques", "overview",
    "introduction", "applications", "issues", "requirements", "fundamentals",
    "features", "types", "methods", "models", "examples", "definitions",
    "design", "requirement", "requirements", "basic concepts", "concepts and principles",
    "case study", "note", "implementation", "analysis", "management", "process",
}
TRAILING_NOISE_RE = re.compile(r"\s+(overview|introduction|basics)$", re.I)
KEEP_TOGETHER = ("advantages and", "and limitations", "pros and", "and cons",
                 "and disadvantages", "and applications", "and types")


LEADING_FILLER_RE = re.compile(r"^(a|an|the|different|various|other|some|all)\s+", re.I)
META_PREFIX_RE = re.compile(
    r"^(defining|guidelines for( creating)?|principles of|levels of|approaches to|"
    r"strategic approach for|goals of|steps (in|of|for)|role of|need for|concept of|"
    r"strengths? of|weakness(es)? of)\s+", re.I)
IMPERATIVE_RE = re.compile(
    r"^(note|create|develop|prepare|write|implement|design|construct|apply|understand|"
    r"explain|identify|perform|use|draw|solve|study|learn|demonstrate)\b", re.I)


def clean_topic(topic):
    topic = re.sub(r"\s+", " ", topic or "").strip(" -:,;.")
    topic = re.sub(r"(?<=\S)-\s+", " ", topic)          # "UML- use case" -> "UML use case"
    topic = LEADING_FILLER_RE.sub("", topic)
    topic = META_PREFIX_RE.sub("", topic)
    topic = TRAILING_NOISE_RE.sub("", topic).strip()
    if topic and topic[0].islower():
        topic = topic[0].upper() + topic[1:]
    return topic


def split_compound_topic(topic):
    """'A and B' / 'A & B' -> ['A', 'B'] when both halves look like real topics."""
    low = topic.lower()
    if any(k in low for k in KEEP_TOGETHER):
        return [topic]
    parts = [p.strip() for p in re.split(r"\s+(?:and|&)\s+", topic) if p.strip()]
    if len(parts) > 1 and all(
        len(p.split()) >= 2 or re.search(r"\(?[A-Z]{2,}\)?", p) for p in parts
    ):
        return parts
    return [topic]


def is_vague(topic, subject_name=""):
    t = topic.lower().strip()
    if t in VAGUE_TOPICS:
        return True
    if len(t) < 3 or IMPERATIVE_RE.match(t):
        return True
    words = t.split()
    subject_words = set(re.findall(r"[a-z]+", (subject_name or "").lower()))
    # single word that is just a word of the subject's own name ("Software" in Software Engineering)
    return len(words) == 1 and t in subject_words


def prepare_topics(topics_by_unit, subject_name=""):
    """Clean, split, de-vague and de-duplicate. Never returns an empty pool."""
    seen, cleaned = set(), {}
    for unit, topics in topics_by_unit.items():
        for raw in topics:
            for part in split_compound_topic(clean_topic(raw)):
                part = clean_topic(part)
                if len(part) < 3 or len(part.split()) > 12 or is_vague(part, subject_name) or part.lower() in seen \
                        or part.lower() == (subject_name or '').lower():
                    continue
                seen.add(part.lower())
                cleaned.setdefault(unit, []).append(part)
    if cleaned:
        return cleaned
    # Syllabus was too sparse to filter: fall back to the raw topics.
    return {u: list(t) for u, t in topics_by_unit.items() if t}


# --------------------------------------------------------- topic type + bank
TYPE_PATTERNS = [
    ("attack", r"\b(attacks?|threats?|vulnerabilit\w+|malware|intrusion)\b"),
    ("ds", r"\b(stacks?|queues?|linked lists?|binary trees?|avl trees?|b\+? ?trees?|"
           r"search trees?|spanning trees?|heaps?|arrays?|hashing|hash tables?|dictionar\w+|"
           r"tuples?)\b"),
    ("code", r"\b(loops?|pointers?|functions?|classes|class|objects?|inheritance|"
             r"polymorphism|encapsulation|recursion|strings?|operators?|statements?|"
             r"exceptions?|threads?|interfaces?|file handling|file i/o|variables?|"
             r"constructors?|data types?|expressions?|lambda|"
             r"decorators?|generators?|comprehensions?|queries|sql|joins?|"
             r"html|css|javascript|dom)\b"),
    ("math", r"\b(theorem|numbers?|equation|probability|matrix|matrices|modular|arithmetic|"
             r"algebra|calculus|transformations?|vectors?|statistics|regression)\b"),
    ("process", r"\b(algorithm|cipher|protocol|standard|technique|method|scheme|transform\w*|"
                r"encryption|signature|exchange|authentication|sort\w*|search\w*|"
                r"normali[sz]ation|scheduling|routing|training|learning|parsing|compil\w+|"
                r"process|life ?cycle|estimation|testing|validation|elicitation|management)\b"),
    ("structure", r"\b(architecture|structure|framework|layers?|topology|component|"
                  r"topology|schema|kernel|pipeline)\b"),
]


def classify_topic(topic):
    t = topic.lower()
    for name, pat in TYPE_PATTERNS:
        if re.search(pat, t):
            return name
    return "concept"


# (bloom_level, template). No pronouns, so singular and plural topics both read well.
QUESTION_BANK = {
    2: {
        "concept": [("Remember", "Define {t}."), ("Remember", "What is meant by {t}?"),
                    ("Remember", "State the purpose of {t}."),
                    ("Remember", "List any two features of {t}.")],
        "process": [("Remember", "Define {t}."), ("Remember", "State the purpose of {t}."),
                    ("Remember", "Mention any two applications of {t}."),
                    ("Remember", "List any two features of {t}.")],
        "structure": [("Remember", "Define {t}."), ("Remember", "Name any two components of {t}."),
                      ("Remember", "State the purpose of {t}.")],
        "attack": [("Remember", "Define {t}."), ("Remember", "Give any two examples of {t}."),
                   ("Remember", "Mention any two countermeasures against {t}.")],
        "math": [("Remember", "Define {t}."), ("Remember", "State any two properties of {t}."),
                 ("Remember", "Mention any two uses of {t}.")],
        "ds": [("Remember", "Define {t}."), ("Remember", "List any two operations on {t}."),
               ("Remember", "Mention any two applications of {t}.")],
        "code": [("Remember", "Define {t}."), ("Remember", "State the purpose of {t}."),
                 ("Remember", "Mention any two uses of {t}.")],
    },
    5: {
        "concept": [("Understand", "Explain {t} with a suitable example."),
                    ("Analyze", "Discuss the advantages and limitations of {t}."),
                    ("Understand", "Describe the importance of {t} in practical applications.")],
        "process": [("Apply", "Explain the working of {t} with a suitable example."),
                    ("Understand", "Describe the steps involved in {t}."),
                    ("Analyze", "Discuss the advantages and limitations of {t}.")],
        "structure": [("Understand", "Describe the main components of {t} and their functions."),
                      ("Understand", "Explain the structure of {t} with a neat diagram."),
                      ("Analyze", "Discuss the advantages and limitations of {t}.")],
        "attack": [("Understand", "Explain how {t} are carried out and how they can be prevented."),
                   ("Understand", "Classify {t} with suitable examples.")],
        "math": [("Understand", "Explain {t} with a suitable example."),
                 ("Apply", "Solve a simple example that uses {t}."),
                 ("Understand", "Describe the role of {t} in the subject.")],
        "ds": [("Understand", "Explain the operations of {t} with a suitable example."),
               ("Apply", "Illustrate {t} with a neat diagram."),
               ("Analyze", "Discuss the advantages and limitations of {t}.")],
        "code": [("Apply", "Explain {t} with a suitable program or code example."),
                 ("Understand", "Explain the concept and usage of {t} with an example."),
                 ("Analyze", "Discuss the advantages and limitations of {t}.")],
    },
    10: {
        "concept": [("Analyze", "Explain {t} in detail with suitable examples."),
                    ("Analyze", "Analyze the advantages, limitations and applications of {t}."),
                    ("Evaluate", "Discuss the principles and applications of {t}.")],
        "process": [("Apply", "Explain the working of {t} in detail with a suitable example."),
                    ("Evaluate", "Analyze the strengths and weaknesses of {t}."),
                    ("Analyze", "Discuss the steps, advantages and limitations of {t}.")],
        "structure": [("Analyze", "Explain the architecture of {t} in detail."),
                      ("Analyze", "Analyze the components of {t} and how they interact."),
                      ("Create", "Discuss the design and working of {t}.")],
        "attack": [("Analyze", "Explain the types of {t} and discuss the countermeasures."),
                   ("Evaluate", "Analyze the impact of {t} and how they can be prevented.")],
        "math": [("Apply", "Explain {t} in detail with worked examples."),
                 ("Analyze", "Discuss {t}, with applications and suitable examples.")],
        "ds": [("Analyze", "Explain the representation and operations of {t} in detail with examples."),
               ("Analyze", "Analyze the advantages, limitations and applications of {t}."),
               ("Apply", "Discuss the implementation of {t} with a suitable example.")],
        "code": [("Apply", "Explain {t} in detail with suitable programs."),
                 ("Analyze", "Discuss the types and uses of {t} with suitable examples."),
                 ("Evaluate", "Analyze the advantages and limitations of {t} with examples.")],
    },
}

MARK_BUCKETS = (2, 5, 10)


def bucket_for(marks):
    return 2 if marks <= 3 else 5 if marks <= 6 else 10


# ------------------------------------------------------------- validation
def validate_question(text, topic):
    if not text or topic.lower() not in text.lower():
        return False
    if "  " in text or "{" in text or "}" in text:
        return False
    if not text.rstrip().endswith((".", "?")):
        return False
    if len(text.split()) < 4 or len(text.split()) > 40:
        return False
    return True


# --------------------------------------------------------------- topic pool
class TopicPool:
    """Hands out topics per unit without repeats until the whole syllabus is used."""

    def __init__(self, topics_by_unit, subject_name=""):
        self.by_unit = prepare_topics(topics_by_unit, subject_name)
        self.units = sorted(self.by_unit)
        self.used = set()

    def _unused(self, unit):
        return [t for t in self.by_unit.get(unit, []) if t.lower() not in self.used]

    def next_topic(self, unit, prefer_words=None):
        candidates = self._unused(unit)
        if not candidates:  # unit exhausted -> borrow from any other unit
            for other in random.sample(self.units, len(self.units)):
                candidates = self._unused(other)
                if candidates:
                    break
        if not candidates:  # whole syllabus used -> allow repeats
            self.used.clear()
            candidates = list(self.by_unit.get(unit) or
                              [t for ts in self.by_unit.values() for t in ts])
        if prefer_words:
            good = [t for t in candidates if len(t.split()) >= prefer_words]
            candidates = good or candidates
        topic = random.choice(candidates)
        self.used.add(topic.lower())
        return topic

    def unit_of(self, topic):
        for unit, topics in self.by_unit.items():
            if topic in topics:
                return unit
        return self.units[0]


def allocate_evenly(items, number):
    items = list(items)
    if not items or number <= 0:
        return []
    base, extra = divmod(number, len(items))
    counts = {i: base for i in items}
    for i in random.sample(items, extra):
        counts[i] += 1
    seq = []
    while any(counts.values()):
        order = [i for i in items if counts[i] > 0]
        random.shuffle(order)
        for i in order:
            seq.append(i)
            counts[i] -= 1
    return seq


def form_question(topic, marks, used_questions):
    """Return (text, bloom_level) or None if every template for this topic is used."""
    pool = list(QUESTION_BANK[bucket_for(marks)][classify_topic(topic)])
    random.shuffle(pool)
    for bloom, tpl in pool:
        if "diagram" in tpl and re.search(r"diagram|dfd|uml", topic, re.I):
            continue
        text = tpl.format(t=topic)
        key = text.lower()
        if key not in used_questions and validate_question(text, topic):
            used_questions.add(key)
            return text, bloom
    return None


def build_question(pool, unit, marks, used_questions, max_tries=40):
    prefer = 2 if bucket_for(marks) > 2 else None
    for _ in range(max_tries):
        topic = pool.next_topic(unit, prefer_words=prefer)
        result = form_question(topic, marks, used_questions)
        if result:
            text, bloom = result
            return {"text": text, "marks": marks, "bloom_level": bloom,
                    "unit": f"Unit {pool.unit_of(topic) + 1}", "topics": [topic]}
    raise ValueError("The syllabus does not have enough distinct topics for this many "
                     "questions. Upload a fuller syllabus or choose fewer total marks.")


# --------------------------------------------------------------------- MCQ
def build_mcq(pool, unit, marks, used_questions, subject_name):
    """Topic-membership MCQs that work for any subject:
       'in'  : which topic IS covered in Unit N   (answer = a topic of Unit N)
       'not' : which topic is NOT covered in Unit N (answer = a topic from another unit)
    Distractors are always real topics from the same syllabus."""
    for _ in range(80):
        topic = pool.next_topic(unit)
        u = pool.unit_of(topic)
        own = list(pool.by_unit.get(u, []))
        own_l = {t.lower() for t in own}
        others = [t for ts_u, ts in pool.by_unit.items() if ts_u != u
                  for t in ts if t.lower() not in own_l]
        kind = random.choice(("in", "not"))
        if kind == "not" and len(own) >= 3 and others:
            correct = random.choice(others)
            distractors = random.sample([t for t in own], 3)
            stem = f"Which of the following topics is NOT covered in Unit {u + 1} of {subject_name}?"
        elif len(others) >= 3:
            correct = topic
            distractors = random.sample(others, 3)
            stem = f"Which of the following topics is covered in Unit {u + 1} of {subject_name}?"
        else:
            continue
        key = f"{stem}|{correct}".lower()
        if key in used_questions:
            continue
        used_questions.add(key)
        options = distractors + [correct]
        random.shuffle(options)
        return {"text": stem, "options": options, "answer": correct, "marks": marks,
                "unit": f"Unit {u + 1}", "topics": [correct]}
    raise ValueError("The syllabus needs at least two units with several topics each "
                     "to build MCQs. Upload a fuller syllabus or choose another pattern.")


# ----------------------------------------------------------------- Part C
def build_part_c(pool, unit, used_questions):
    if random.random() < 0.5:                      # single 10-mark question
        q = build_question(pool, unit, 10, used_questions)
        q.update({"section": "Part C", "subquestions": []})
        return q
    subs = []                                       # (a) 5 + (b) 5, same unit
    for _ in range(2):
        s = build_question(pool, unit, 5, used_questions)
        subs.append(s)
    return {"text": "", "marks": 10, "section": "Part C", "unit": subs[0]["unit"],
            "bloom_level": "/".join(s["bloom_level"] for s in subs),
            "subquestions": subs, "topics": [s["topics"][0] for s in subs]}


# ------------------------------------------------------- pattern -> marks
PATTERN_MARKS = {"MCQ": 1, "Short": 2, "Medium": 5, "Long": 10}


def mixed_structure(total_marks):
    """Mixed = Part A (2m) + Part B (5m) + Part C (10m). Returns None if unsupported."""
    total = int(total_marks)
    if total % 10 or total < 20 or total > 100:
        return None
    if total >= 50:
        a, b, c = 10, 4, (total - 40) // 10
    elif total == 40:
        a, b, c = 5, 2, 2
    elif total == 30:
        a, b, c = 5, 2, 1
    else:  # 20
        a, b, c = 5, 2, 0
    parts = {
        "Part A": {"attempt": a, "given": a + 2 if a >= 10 else a + 1, "marks": 2},
        "Part B": {"attempt": b, "given": b + 1, "marks": 5},
    }
    if c:
        parts["Part C"] = {"attempt": c, "given": c + 1, "marks": 10}
    assert sum(p["attempt"] * p["marks"] for p in parts.values()) == total
    return parts


def plan(pattern, total_marks):
    """Single source of truth for marks per question and question count.
    Used for every exam type (SEE / CIA / Other) and every generation mode."""
    total = int(total_marks)
    if pattern == "Mixed":
        parts = mixed_structure(total)
        if parts is None:
            raise ValueError("Mixed pattern (2, 5 and 10 mark questions) needs total marks "
                             "that are a multiple of 10, from 20 to 100.")
        return {"pattern": "Mixed", "marks_each": None, "parts": parts,
                "number": sum(p["given"] for p in parts.values())}
    if pattern not in PATTERN_MARKS:
        raise ValueError(f"Unknown question pattern: {pattern}")
    marks = PATTERN_MARKS[pattern]
    if total % marks:
        raise ValueError(f"{pattern} questions carry {marks} mark(s) each, so total marks "
                         f"must be a multiple of {marks}.")
    return {"pattern": pattern, "marks_each": marks, "parts": None, "number": total // marks}


def sort_by_unit(questions):
    """Keep Part A -> B -> C order; inside each part (or the whole paper) go Unit 1, 2, 3..."""
    rank = {"Part A": 0, "Part B": 1, "Part C": 2}

    def key(q):
        try:
            unit_no = int(str(q.get("unit", "Unit 0")).split()[-1])
        except ValueError:
            unit_no = 0
        return (rank.get(q.get("section"), 0), unit_no)

    return sorted(questions, key=key)      # stable: ties keep their random order


# ------------------------------------------------------------ main entrance
def generate(subject_name, topics_by_unit, pattern, total_marks):
    p = plan(pattern, total_marks)
    pool = TopicPool(topics_by_unit, subject_name)
    used_questions = set()
    questions = []

    if p["parts"]:
        for name, part in p["parts"].items():
            for unit in allocate_evenly(pool.units, part["given"]):
                if name == "Part C":
                    questions.append(build_part_c(pool, unit, used_questions))
                else:
                    q = build_question(pool, unit, part["marks"], used_questions)
                    q.update({"section": name, "subquestions": []})
                    questions.append(q)
        return sort_by_unit(questions)

    for unit in allocate_evenly(pool.units, p["number"]):
        if pattern == "MCQ":
            questions.append(build_mcq(pool, unit, 1, used_questions, subject_name))
        else:
            questions.append(build_question(pool, unit, p["marks_each"], used_questions))
    return sort_by_unit(questions)