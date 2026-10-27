"""AquaAssist front end: the dusk dive.

app.py does the lesson-plan work and runs the four steps (program, level, skills,
plan). Everything about how it looks lives here and in assets/style.css, so the
look can change without touching that logic.
"""
import base64
import html
import re
import time
from contextlib import contextmanager
from pathlib import Path

import streamlit as st

ASSETS_DIR = Path(__file__).parent / "assets"

# ---- Things you may want to change ----
MIN_LOADING_SECONDS = 1.6  # keeps the bubbles visible long enough to see; 0 turns this off

STEPS = [("program", "Program"), ("level", "Level"), ("skills", "Skills"), ("plan", "Plan")]
DEPTH = {"program": "0 m", "level": "10 m", "skills": "20 m", "plan": "30 m"}
# Where the water surface sits on screen. Each step sinks further until the plan is fully underwater.
HORIZON = {"program": "26vh", "level": "15vh", "skills": "5vh", "plan": "-15vh"}


# ------------------ Assets ------------------

@st.cache_data(show_spinner=False)
def _data_uri(name):
    data = base64.b64encode((ASSETS_DIR / name).read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


@st.cache_data(show_spinner=False)
def _stylesheet():
    """style.css with every url("asset:file.png") swapped for an inline image."""
    css = (ASSETS_DIR / "style.css").read_text(encoding="utf-8")
    return re.sub(r'url\("asset:([^"]+)"\)', lambda m: f'url("{_data_uri(m.group(1))}")', css)


def _img(name, css_class):
    # Decorative, so the alt text is empty.
    return f'<img class="{css_class}" src="{_data_uri(name)}" alt="">'


# ------------------ Page setup ------------------

def setup_page(step):
    """Page config, stylesheet, and where the water surface sits for this step.
    Call before any other Streamlit command."""
    try:
        from PIL import Image
        icon = Image.open(ASSETS_DIR / "dolphin_px.png")
    except Exception:
        icon = "🐬"
    st.set_page_config(page_title="AquaAssist", page_icon=icon, layout="wide")
    horizon = HORIZON.get(step, HORIZON["program"])
    st.markdown(
        f"<style>{_stylesheet()}:root{{--aa-horizon:{horizon};}}</style>",
        unsafe_allow_html=True,
    )


def show_splash():
    """Loading screen, once per browser session. It fades out on its own
    after about two seconds (a CSS animation), so it never blocks the app."""
    if st.session_state.get("_splash_done"):
        return
    st.session_state["_splash_done"] = True
    st.markdown(
        '<div class="aa-splash" role="status" aria-label="Loading AquaAssist">'
        '<div class="aa-splash-title">AquaAssist</div>'
        f'{_img("dolphin_px.png", "aa-mascot")}'
        '<div class="aa-splash-bar"></div>'
        "</div>",
        unsafe_allow_html=True,
    )


# ------------------ Header ------------------

def render_header(step):
    """Logo, tagline, the four steps, the depth readout, and the scene overlays."""
    current = [key for key, _ in STEPS].index(step)
    items = []
    for i, (_, label) in enumerate(STEPS):
        if i < current:
            items.append(f'<li class="is-done">{label}</li>')
        elif i == current:
            items.append(f'<li class="is-current" aria-current="step">{label}</li>')
        else:
            items.append(f"<li>{label}</li>")
    st.markdown(
        '<div class="aa-scan" aria-hidden="true"></div>'
        f'{_img("dolphin_px.png", "aa-float")}'
        '<div class="aa-top">'
        '<div class="aa-brand">'
        '<div class="aa-logo" role="heading" aria-level="1">AquaAssist</div>'
        f'<div class="aa-meta">Step {current + 1} of {len(STEPS)}</div>'
        "</div>"
        '<p class="aa-tagline">Pick a program, a level and your skills. Get a lesson plan.</p>'
        '<div class="aa-hud">'
        f'<ol class="aa-steps" aria-label="Steps">{"".join(items)}</ol>'
        f'<div class="aa-depth">Depth {DEPTH[step]}</div>'
        "</div></div>",
        unsafe_allow_html=True,
    )


def heading(title, hint=None):
    hint_html = f'<p class="aa-hint">{html.escape(hint)}</p>' if hint else ""
    st.markdown(
        f'<div class="aa-heading" role="heading" aria-level="2">{html.escape(title)}</div>{hint_html}',
        unsafe_allow_html=True,
    )


def label(text):
    """Small tracked label with a dashed rule, like 'DIVE LOG' on Krillion."""
    st.markdown(f'<div class="aa-label">{html.escape(text)}</div>', unsafe_allow_html=True)


def count(text):
    st.markdown(f'<p class="aa-count">{html.escape(text)}</p>', unsafe_allow_html=True)


def big_stats(stats):
    """Large pixel numerals. stats: [(value, unit, caption)], e.g. [("10", "min", "Lesson time")]."""
    cells = []
    for value, unit, caption in stats:
        unit_html = f'<span class="aa-unit">{html.escape(unit)}</span>' if unit else ""
        cells.append(
            f'<div class="aa-stat"><div class="aa-big">{html.escape(str(value))}{unit_html}</div>'
            f'<div class="aa-cap">{html.escape(caption)}</div></div>'
        )
    st.markdown(f'<div class="aa-stats">{"".join(cells)}</div>', unsafe_allow_html=True)


def item_list(items):
    rows = "".join(f"<li>{html.escape(i)}</li>" for i in items)
    st.markdown(f'<ul class="aa-list">{rows}</ul>', unsafe_allow_html=True)


def pdf_frame(pdf_bytes, height=650):
    """Inline preview of the generated PDF."""
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    st.markdown(
        '<div class="aa-frame">'
        f'<iframe title="Lesson plan preview" src="data:application/pdf;base64,{b64}" '
        f'width="100%" height="{height}"></iframe></div>',
        unsafe_allow_html=True,
    )


# ------------------ Loading animation ------------------

def _loader_html():
    return (
        '<div class="aa-loader" role="status">'
        f'{_img("dolphin_px.png", "aa-mascot")}'
        '<div class="aa-bubbles"><span class="aa-bubble"></span>'
        '<span class="aa-bubble"></span><span class="aa-bubble"></span></div>'
        '<p class="aa-loader-text">Building your plan</p>'
        "</div>"
    )


@contextmanager
def loading():
    """Show the dolphin and three popping bubbles while the code inside the block runs."""
    slot = st.empty()
    slot.markdown(_loader_html(), unsafe_allow_html=True)
    start = time.time()
    try:
        yield
    finally:
        wait = MIN_LOADING_SECONDS - (time.time() - start)
        if wait > 0:
            time.sleep(wait)
        slot.empty()
