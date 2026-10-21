import os
import re
import math
import base64
import streamlit as st
import pdfplumber
from docx import Document
from fpdf import FPDF
from gensim.models import Word2Vec
from gensim.utils import simple_preprocess
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

# ------------------ CONFIG ------------------

st.set_page_config(page_title="AquaAssist 1.1", layout="centered")
st.title("AquaAssist 1.1")
st.caption("Example: **'Tiny Tots Level 5 — Back Crawl, Front Crawl'**")

LEVELS_DIR = "skill_tracking"       # PDFs named after levels e.g. "Tiny Tots Level 5 Sting Rays Must Sees.pdf"
PROGRESSIONS_DIR = "lesson_progressions"  # Docx files named after skills e.g. "Back Crawl.docx"
OUTPUT_DIR = "output"
FONT_DIR = "fonts"  # put DejaVuSans.ttf and DejaVuSans-Bold.ttf here
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ------------------ FILE DISCOVERY ------------------

def get_all_progression_names():
    """Return list of skill names from docx filenames (without extension)."""
    if not os.path.exists(PROGRESSIONS_DIR):
        return []
    return [
        os.path.splitext(f)[0]
        for f in os.listdir(PROGRESSIONS_DIR)
        if f.lower().endswith(".docx")
    ]

def extract_level_number(text):
    """Pull the first number found in the text, e.g. 'Swimmer 4' -> '4',
    'Junior Masters 4' -> '4', 'Tiny Tots Level 5' -> '5'.

    Generalized from a 'level'-only regex so the same exact-number-match
    logic works across all naming conventions (Level, Swimmer, Junior
    Masters, etc.) instead of just filenames containing the word 'level'.
    """
    match = re.search(r"\d+", text)
    return match.group(0) if match else None

def get_level_pdf_path(user_input):
    """Match user input to a level PDF filename.

    Prioritizes an exact level-number match (e.g. 'Level 5' only matches
    filenames containing 'Level 5', not 'Level 1', 'Level 15', etc.) before
    falling back to generic word overlap. This avoids the old bug where
    every level shared common words like 'tiny'/'tots'/'level' and the
    first PDF in the OS listing order would win regardless of which level
    number was actually requested.
    """
    if not os.path.exists(LEVELS_DIR):
        return None, None

    normalized = user_input.lower().replace(" ", "_")
    user_level_num = extract_level_number(user_input)

    candidates = []
    for f in os.listdir(LEVELS_DIR):
        if not f.lower().endswith(".pdf"):
            continue
        clean_name = os.path.splitext(f)[0].lower().replace("_must_sees", "").strip("_")
        display_name = clean_name.replace("_", " ").title()
        file_level_num = extract_level_number(clean_name.replace("_", " "))
        words = [w for w in clean_name.split("_") if len(w) > 3]
        overlap = sum(1 for w in words if w in normalized)
        candidates.append((f, display_name, file_level_num, overlap))

    # Prefer an exact level-number match, breaking ties with word overlap
    if user_level_num:
        exact_matches = [c for c in candidates if c[2] == user_level_num]
        if exact_matches:
            best = max(exact_matches, key=lambda c: c[3])
            return os.path.join(LEVELS_DIR, best[0]), best[1]

    # Fallback: best generic word overlap (only if something actually matched)
    if candidates:
        best = max(candidates, key=lambda c: c[3])
        if best[3] > 0:
            return os.path.join(LEVELS_DIR, best[0]), best[1]

    return None, None

def get_must_sees_from_level(pdf_path):
    """Extract all must-see skill names from a level PDF."""
    must_sees = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                for line in text.split("\n"):
                    line = line.strip().lstrip("•").strip()
                    if line and not line.endswith(":"):  # skip category headers
                        must_sees.append(line)
    return must_sees


# Alternate names swimmers/parents commonly use that should be treated as
# the same skill as the canonical docx/progression name. Add more pairs
# here as needed (alias -> canonical name, case-insensitive).
# Alternate names swimmers/parents commonly use that should be treated as
# the same skill as the canonical docx/progression name. Add more pairs
# here as needed (alias -> canonical name, case-insensitive).
SKILL_SYNONYMS = {
    "freestyle": "front crawl",
    "backstroke": "back crawl",
    "back paddle": "back kick w scull/paddle",
    "front glide": "glide on front",
    "back glide": "glide on back",
    "front float": "float on front",
    "back float": "float on back",
    "flutter kick": "flutter kick on front",
    "back kick": "flutter kick on back",
    "side glide": "flutter kick on side",
    "treading water": "tread water",
    "legs-only surface support": "legs only tread water",
    "legs only surface support": "legs only tread water",
    "jump entry into deep water and tread": "jump entry into deep water and tread water",
}

# Some short aliases above are also literal prefixes of *other* distinct
# skill names (e.g. "back kick" is a prefix of "back kick w scull/paddle",
# and "flutter kick" is a prefix of "flutter kick on back/side"). Without
# an exclusion, substituting the short alias would corrupt those longer,
# unrelated names. Each entry here is a negative-lookahead suffix appended
# to that alias's match pattern so it only fires when NOT followed by the
# start of one of those longer names.
_SYNONYM_EXCLUDE_AFTER = {
    "back kick": r"(?!\s*w\s*scull/?paddle)",
    "flutter kick": r"(?!\s*on\s*(?:back|front|side))",
    "jump entry into deep water and tread": r"(?!\s*water)",
}

def canonicalize_skill_text(text):
    """Replace any whole-word synonym occurrence with its canonical name,
    e.g. 'Freestyle 15m' -> 'front crawl 15m'. Case-insensitive; operates
    on lowercased text with word boundaries. Longest aliases are applied
    first so multi-word aliases win over any shorter alias that happens to
    be one of their prefixes, and _SYNONYM_EXCLUDE_AFTER further protects
    short aliases from matching inside unrelated longer skill names."""
    result = text.lower()
    for alias in sorted(SKILL_SYNONYMS, key=len, reverse=True):
        canonical = SKILL_SYNONYMS[alias]
        exclude = _SYNONYM_EXCLUDE_AFTER.get(alias, "")
        pattern = r"\b" + re.escape(alias) + r"\b" + exclude
        result = re.sub(pattern, canonical, result)
    return result


def normalize_name(text):
    """Canonicalize synonyms, then lowercase and strip all non-alphanumeric
    characters (including spaces) for loose skill-name matching — makes
    'Front Crawl', 'FrontCrawl', and 'Freestyle' all resolve to the same
    key ('frontcrawl')."""
    return re.sub(r"[^a-z0-9]", "", canonicalize_skill_text(text))


# Matches the first number+unit occurring anywhere in a line, e.g.:
#   "Front crawl 15m"                        -> "15"
#   "Front Crawl 5m with bilateral breathing" -> "5"   (extra text after)
#   "Unassisted rocketship (5m)"              -> "5"   (parentheses)
#   "Tread water 30 sec."                     -> "30"
_NUMBER_UNIT_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(m|sec\.?|secs\.?|seconds?|min\.?|mins\.?|minutes?)\b",
    re.IGNORECASE,
)

def _value_from_match(match):
    """Return the number as a string, converting minutes to seconds
    (e.g. '1 min' -> '60') since time blanks in the lesson plan are in
    seconds. Meters and seconds are returned as-is."""
    number, unit = match.group(1), match.group(2).lower()
    if unit.startswith("min"):
        seconds = float(number) * 60
        return str(int(seconds)) if seconds == int(seconds) else f"{seconds:g}"
    return number

def _unit_kind(unit):
    """'time' for seconds/minutes, 'distance' for meters."""
    return "time" if unit.lower().startswith(("sec", "min")) else "distance"

# Finds the unit written right after a '_' blank in a progression line,
# e.g. 'Shoulder roll w KB _ m (2 min)' -> 'm', 'Front Crawl _ x 2 m' -> 'm',
# 'Tread water _ sec' -> 'sec'. Stops at '(' so the '(X min)' time tag is ignored.
_BLANK_UNIT_PATTERN = re.compile(
    r"_[^()]*?\b(m|sec\.?|secs\.?|seconds?|min\.?|mins\.?|minutes?)\b",
    re.IGNORECASE,
)

def blank_kind(line):
    """What kind of value a '_' blank in this line expects: 'distance'
    (meters), 'time' (seconds/minutes), or None if no unit follows it."""
    match = _BLANK_UNIT_PATTERN.search(line)
    return _unit_kind(match.group(1)) if match else None


def find_distance_for_skill(skill_name, must_sees_lines, kind=None):
    """Find the must-sees line mentioning this skill and pull its first
    number+unit, wherever it falls in the line (not just at the end).

    Real must-sees PDFs are inconsistent — some lines end right after the
    distance, some have trailing descriptive text, some wrap the distance
    in parentheses. Matching on 'contains the skill name' + 'first number
    found in that line' handles all of those instead of requiring an exact
    line format.

    If kind is 'distance' or 'time', only numbers with that kind of unit
    are considered, so a blank that expects meters never picks up a
    seconds value (and vice versa). kind=None accepts either.
    """
    target = normalize_name(skill_name)
    if not target:
        return None

    normalized_lines = [(line, normalize_name(line)) for line in must_sees_lines]

    # Prefer lines that START with the skill name (so 'Tread Water' picks
    # 'Tread water 30 sec.' rather than a longer line that merely contains
    # it, like 'Legs-only surface support'); fall back to 'contains'.
    for matches_line in (
        lambda norm: norm.startswith(target),
        lambda norm: target in norm,
    ):
        for line, norm in normalized_lines:
            if matches_line(norm):
                for match in _NUMBER_UNIT_PATTERN.finditer(line):
                    if kind is None or _unit_kind(match.group(2)) == kind:
                        return _value_from_match(match)
    return None


# ------------------ WORD2VEC MATCHING ------------------

@st.cache_resource
def build_word2vec_model():
    """Build Word2Vec model from all progression names."""
    skill_names = get_all_progression_names()
    if not skill_names:
        return None, []
    corpus = [simple_preprocess(name) for name in skill_names]
    model = Word2Vec(corpus, vector_size=50, window=3, min_count=1, workers=2, epochs=100)
    return model, skill_names

def get_sentence_vector(model, tokens):
    """Average word vectors to get a sentence vector."""
    vectors = [model.wv[t] for t in tokens if t in model.wv]
    if not vectors:
        return np.zeros(model.vector_size)
    return np.mean(vectors, axis=0)

def match_skill(query, model, skill_names):
    """
    Match a user query to the closest skill name using:
    1. Exact match first (synonym-aware, e.g. 'Freestyle' == 'Front Crawl')
    2. Word2Vec cosine similarity as fallback
    """
    query_clean = query.strip().lower()
    query_canonical = canonicalize_skill_text(query_clean)

    # Exact match (synonym-aware)
    for skill in skill_names:
        if canonicalize_skill_text(skill.lower()) == query_canonical:
            return skill

    # Partial match (synonym-aware)
    for skill in skill_names:
        skill_canonical = canonicalize_skill_text(skill.lower())
        if query_canonical in skill_canonical or skill_canonical in query_canonical:
            return skill

    # Word2Vec semantic match
    if model is None:
        return None

    query_tokens = simple_preprocess(query_canonical)
    query_vec = get_sentence_vector(model, query_tokens).reshape(1, -1)

    best_skill = None
    best_score = -1

    for skill in skill_names:
        skill_tokens = simple_preprocess(skill.lower())
        skill_vec = get_sentence_vector(model, skill_tokens).reshape(1, -1)
        score = cosine_similarity(query_vec, skill_vec)[0][0]
        if score > best_score:
            best_score = score
            best_skill = skill

    # Only return if similarity is above threshold
    return best_skill if best_score > 0.4 else None


# ------------------ PROGRESSION PARSING ------------------

def get_progressions_for_skill(skill_name):
    """Read progressions list from the skill's docx file."""
    path = os.path.join(PROGRESSIONS_DIR, f"{skill_name}.docx")
    if not os.path.exists(path):
        return []
    doc = Document(path)
    progressions = []
    for para in doc.paragraphs:
        text = para.text.strip().lstrip("-").strip()
        if text:
            progressions.append(text)
    return progressions


# ------------------ EQUIPMENT DETECTION ------------------

# Maps a lowercase keyword (matched as a whole word) to its display label.
# "kb" is the common shorthand for kickboard seen in progression text.
EQUIPMENT_KEYWORDS = {
    "marshmallow": "Marshmallow",
    "marshmallows": "Marshmallow",
    "kickboard": "Kickboard",
    "kickboards": "Kickboard",
    "kb": "Kickboard",
    "noodle": "Noodle",
    "noodles": "Noodle",
    "mat": "Mat",
    "mats": "Mat",
}

def detect_equipment(skills_and_progressions):
    """Scan skill names + progression text for equipment keywords.

    Uses whole-word matching (not substring) so e.g. 'mat' doesn't
    false-positive inside words like 'format' or 'automatic'.
    """
    combined_text = " ".join(
        skill + " " + " ".join(progressions)
        for skill, progressions in skills_and_progressions.items()
    ).lower()

    found = set()
    for keyword, label in EQUIPMENT_KEYWORDS.items():
        if re.search(r"\b" + re.escape(keyword) + r"\b", combined_text):
            found.add(label)

    return sorted(found)


# ------------------ TIME CALCULATION ------------------

_TIME_TAG_PATTERN = re.compile(r"\((\d+(?:\.\d+)?)\s*min\)", re.IGNORECASE)

_SECONDS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:sec\.?|secs\.?|seconds?)\b", re.IGNORECASE)

def calculate_total_minutes(skills_and_progressions):
    """Total lesson minutes: every '(X min)' time tag, plus the seconds-based
    progressions (e.g. treading water) — those seconds are added together,
    divided by 60, and rounded up. Lines that already carry a '(X min)' tag
    are counted by the tag only, so nothing is double counted."""
    total_minutes = 0.0
    total_seconds = 0.0
    for progressions in skills_and_progressions.values():
        for line in progressions:
            tag = _TIME_TAG_PATTERN.search(line)
            if tag:
                total_minutes += float(tag.group(1))
                continue
            seconds = _SECONDS_PATTERN.search(line)
            if seconds:
                total_seconds += float(seconds.group(1))
    return total_minutes + math.ceil(total_seconds / 60)


# ------------------ PDF GENERATION ------------------

class LessonPlanPDF(FPDF):
    def __init__(self):
        super().__init__()
        # Register Unicode fonts so em dashes, smart quotes, accents, etc.
        # never blow up FPDF's default Latin-1 encoder.
        regular = os.path.join(FONT_DIR, "DejaVuSans.ttf")
        bold = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
        italic = os.path.join(FONT_DIR, "DejaVuSans-Oblique.ttf")

        self.add_font("DejaVu", "", regular)
        self.add_font("DejaVu", "B", bold if os.path.exists(bold) else regular)
        self.add_font("DejaVu", "I", italic if os.path.exists(italic) else regular)

    def header(self):
        start_y = self.get_y()

        # Total lesson time, top-right corner
        if self.total_time:
            self.set_font("DejaVu", "", 10)
            self.set_text_color(90, 90, 90)
            self.set_xy(-70, start_y)
            self.cell(60, 6, f"Total Time: {self.total_time}", align="R")

        self.set_xy(10, start_y)
        self.set_font("DejaVu", "B", 14)
        self.set_text_color(31, 78, 121)  # Navy blue
        self.cell(0, 10, self.title, ln=True, align="C")
        self.set_draw_color(31, 78, 121)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("DejaVu", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"AquaAssist 2.0 — Page {self.page_no()}", align="C")

def generate_lesson_plan_pdf(level_name, skills_and_progressions, output_path):
    pdf = LessonPDF()
    pdf.title = f"{level_name} — Lesson Plan"

    total_minutes = calculate_total_minutes(skills_and_progressions)
    pdf.total_time = (
        f"{int(total_minutes)} min" if total_minutes == int(total_minutes) else f"{total_minutes:g} min"
    ) if total_minutes > 0 else None

    pdf.add_page()

    # Equipment Needed section
    equipment = detect_equipment(skills_and_progressions)
    if equipment:
        pdf.set_font("DejaVu", "B", 12)
        pdf.set_fill_color(214, 228, 240)
        pdf.set_text_color(31, 78, 121)
        pdf.cell(0, 8, "Equipment Needed", ln=True, fill=True)
        pdf.ln(1)

        pdf.set_font("DejaVu", "", 10)
        pdf.set_text_color(26, 26, 26)
        pdf.cell(8)
        pdf.cell(0, 7, ", ".join(equipment), ln=True)
        pdf.ln(3)

    for skill, progressions in skills_and_progressions.items():
        # Skill header
        pdf.set_font("DejaVu", "B", 12)
        pdf.set_fill_color(214, 228, 240)
        pdf.set_text_color(31, 78, 121)
        pdf.cell(0, 8, skill, ln=True, fill=True)
        pdf.ln(1)

        # Progressions
        pdf.set_font("DejaVu", "", 10)
        pdf.set_text_color(26, 26, 26)
        if progressions:
            for i, prog in enumerate(progressions, start=1):
                pdf.cell(8)  # indent
                pdf.cell(0, 7, f"{i}. {prog}", ln=True)
        else:
            pdf.cell(8)
            pdf.set_text_color(150, 150, 150)
            pdf.cell(0, 7, "No progressions found for this skill.", ln=True)
            pdf.set_text_color(26, 26, 26)

        pdf.ln(3)

    pdf.output(output_path)


# Monkey-patch title/total_time onto FPDF since we set them manually
class LessonPDF(LessonPlanPDF):
    title = ""
    total_time = None


# ------------------ PDF DISPLAY ------------------

def display_pdf(path):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    st.markdown(
        f"""
        <iframe
            src="data:application/pdf;base64,{b64}"
            width="100%"
            height="650"
            style="border:none; border-radius:8px;">
        </iframe>
        """,
        unsafe_allow_html=True,
    )


# ------------------ INPUT PARSING ------------------

def parse_user_input(user_input):
    """
    Split user input into level query and requested must-sees.
    Expected format: 'Level Name — Skill 1, Skill 2, Skill 3'
    Also handles 'Level Name: Skill 1, Skill 2' or 'Level Name Skill 1 Skill 2'
    """
    # Try splitting on dash or colon
    for delimiter in [" — ", " - ", ":"]:
        if delimiter in user_input:
            parts = user_input.split(delimiter, 1)
            level_query = parts[0].strip()
            skills_raw = [s.strip() for s in parts[1].split(",")]
            return level_query, skills_raw

    # Fallback: no delimiter, treat whole thing as level query, no specific skills
    return user_input.strip(), []


# ------------------ MAIN UI ------------------

model, skill_names = build_word2vec_model()

user_input = st.chat_input("Enter level and must-sees (e.g. 'Tiny Tots Level 5 — Back Crawl, Front Crawl')")

if user_input:
    level_query, requested_skills = parse_user_input(user_input)
    level_pdf_path, level_name = get_level_pdf_path(level_query)

    with st.chat_message("assistant"):

        if not level_pdf_path:
            st.error("Sorry — couldn't find a level matching that name.")
            if os.path.exists(LEVELS_DIR):
                available = [
                    os.path.splitext(f)[0].replace("_Must_Sees", "").replace("_", " ").title()
                    for f in os.listdir(LEVELS_DIR)
                    if f.endswith(".pdf")
                ]
                st.write("**Available levels:**")
                for lvl in available:
                    st.write(f"- {lvl}")

        else:
            # If no skills specified, show all must-sees for that level
            if not requested_skills:
                st.info(f"No must-sees specified. Here are all must-sees for **{level_name}**:")
                must_sees = get_must_sees_from_level(level_pdf_path)
                for ms in must_sees:
                    st.write(f"- {ms}")
                st.write("Re-enter with specific must-sees to generate a lesson plan.")

            else:
                # Pass 1: match each requested skill, collect raw progressions
                # and whatever values we find, without filling blanks yet.
                # Distances (meters) and times (seconds) are looked up
                # separately so a blank expecting meters never gets a
                # seconds value, or the reverse.
                raw_progressions = {}
                skill_values = {}  # {skill: {"distance": .., "time": .., None: ..}}
                unmatched = []

                must_sees_lines = get_must_sees_from_level(level_pdf_path)

                with st.spinner("Matching skills..."):
                    for skill_query in requested_skills:
                        matched = match_skill(skill_query, model, skill_names)
                        if matched:
                            raw_progressions[matched] = get_progressions_for_skill(matched)
                            skill_values[matched] = {
                                kind: find_distance_for_skill(matched, must_sees_lines, kind)
                                for kind in ("distance", "time", None)
                            }
                        else:
                            unmatched.append(skill_query)

                if unmatched:
                    st.warning(f"Couldn't match these skills: {', '.join(unmatched)}")

                # Flag a genuine conflict if both Front Crawl and Back Crawl
                # were requested with different explicit distances — don't
                # silently override, just let the person know.
                front_key = next(
                    (s for s in raw_progressions if normalize_name(s) == normalize_name("Front Crawl")), None
                )
                back_key = next(
                    (s for s in raw_progressions if normalize_name(s) == normalize_name("Back Crawl")), None
                )
                if front_key and back_key:
                    front_dist = skill_values[front_key]["distance"]
                    back_dist = skill_values[back_key]["distance"]
                    if front_dist and back_dist and front_dist != back_dist:
                        st.warning(
                            f"Front Crawl ({front_dist}m) and Back Crawl ({back_dist}m) have different "
                            "distances in the must-sees PDF — using each as listed. Worth double-checking."
                        )

                # Fallback distance (meters only) for any blank that has none
                # of its own: use Front Crawl's distance for this level (the
                # usual benchmark), or Flutter Kick's if Front Crawl isn't
                # listed either. Looked up independently of what was
                # requested. Blanks measured in seconds (e.g. treading
                # water) never use these, since they're meters.
                fallback_front_crawl = find_distance_for_skill("Front Crawl", must_sees_lines, "distance")
                fallback_flutter_kick = find_distance_for_skill("Flutter Kick", must_sees_lines, "distance")

                # Pass 2: fill in each blank according to the unit written
                # after it, falling back where needed.
                matched_skills = {}
                missing_distance = []
                used_fallback = {}  # {"Front Crawl" | "Flutter Kick": [skill, ...]}

                for skill, progressions in raw_progressions.items():
                    filled = []
                    unfilled = False
                    for p in progressions:
                        if "_" not in p:
                            filled.append(p)
                            continue
                        kind = blank_kind(p)
                        value = skill_values[skill][kind]
                        fallback_source = None
                        if not value and kind != "time":
                            if fallback_front_crawl:
                                value, fallback_source = fallback_front_crawl, "Front Crawl"
                            elif fallback_flutter_kick:
                                value, fallback_source = fallback_flutter_kick, "Flutter Kick"
                        if value:
                            p = p.replace("_", value)
                            if fallback_source and skill not in used_fallback.setdefault(fallback_source, []):
                                used_fallback[fallback_source].append(skill)
                        else:
                            unfilled = True
                        filled.append(p)
                    if unfilled:
                        missing_distance.append(skill)
                    matched_skills[skill] = filled

                for source, skills_list in used_fallback.items():
                    fallback_value = fallback_front_crawl if source == "Front Crawl" else fallback_flutter_kick
                    st.info(
                        f"No distance listed for {', '.join(skills_list)} — used the {source} "
                        f"distance ({fallback_value}m) instead."
                    )

                if missing_distance:
                    st.warning(
                        f"No distance found in the must-sees PDF for: {', '.join(missing_distance)}. "
                        "Those progression lines still show a blank — double check the wording matches."
                    )

                if matched_skills:
                    # Generate PDF
                    safe_level = level_name.replace(" ", "_").replace("/", "-")
                    output_path = os.path.join(OUTPUT_DIR, f"{safe_level}_lesson_plan.pdf")

                    with st.spinner("Generating lesson plan..."):
                        generate_lesson_plan_pdf(level_name, matched_skills, output_path)

                    st.success(f"Lesson plan generated for **{level_name}**!")

                    total_minutes = calculate_total_minutes(matched_skills)
                    if total_minutes > 0:
                        time_display = (
                            f"{int(total_minutes)} min"
                            if total_minutes == int(total_minutes)
                            else f"{total_minutes:g} min"
                        )
                        st.write(f"**Total lesson time:** {time_display}")

                    # Display matched skills summary
                    st.write("**Included must-sees:**")
                    for skill in matched_skills:
                        st.write(f"- {skill}")

                    # Display PDF inline
                    display_pdf(output_path)

                    # Download button
                    with open(output_path, "rb") as f:
                        st.download_button(
                            label="📥 Download Lesson Plan PDF",
                            data=f,
                            file_name=os.path.basename(output_path),
                            mime="application/pdf",
                        )
                else:
                    st.error("No skills could be matched. Please check your input and try again.")
