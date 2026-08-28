"""Centralized visual theme for the Streamlit interface."""

import streamlit as st


APP_CSS = """
<style>
:root {
    --content-max-width: 1440px;
    --space-2: 0.65rem;
    --space-3: 1rem;
    --space-4: 1.5rem;
    --space-5: 2rem;
    --radius-card: 0.75rem;
    --border-soft: color-mix(in srgb, currentColor 14%, transparent);
    --surface-soft: color-mix(in srgb, currentColor 4%, transparent);
}

html, body, [class*="css"] {
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", system-ui, sans-serif;
}

.stMainBlockContainer {
    max-width: var(--content-max-width);
    padding-top: 2rem;
    padding-bottom: 3rem;
}

h1, h2, h3, h4, p, label, button, input, textarea { letter-spacing: 0; }
h1 {
    font-size: clamp(2rem, 3vw, 2.25rem) !important;
    line-height: 1.2 !important;
    font-weight: 700 !important;
    margin: 0 0 var(--space-2) !important;
}
h2 {
    font-size: clamp(1.625rem, 2vw, 1.75rem) !important;
    line-height: 1.3 !important;
    font-weight: 700 !important;
    margin: var(--space-5) 0 var(--space-3) !important;
}
h3 {
    font-size: clamp(1.25rem, 1.6vw, 1.375rem) !important;
    line-height: 1.4 !important;
    font-weight: 600 !important;
    margin: var(--space-4) 0 var(--space-2) !important;
}
h4 {
    font-size: 1.05rem !important;
    line-height: 1.45 !important;
    font-weight: 600 !important;
}
p, li, label, .stMarkdown, [data-testid="stWidgetLabel"] {
    font-size: 0.975rem;
    line-height: 1.6;
}
[data-testid="stCaptionContainer"] p {
    font-size: 0.875rem !important;
    line-height: 1.6 !important;
    opacity: 0.78;
}
[data-testid="stMetric"] {
    min-height: 5.8rem;
    padding: 0.7rem 0.85rem;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-card);
    background: var(--surface-soft);
}
[data-testid="stMetricLabel"] p {
    font-size: 0.825rem !important;
    line-height: 1.35 !important;
    font-weight: 600 !important;
}
[data-testid="stMetricValue"] {
    width: 100% !important;
    overflow: visible !important;
    text-overflow: clip !important;
    white-space: normal !important;
    overflow-wrap: anywhere;
    font-size: clamp(1.375rem, 1.7vw, 1.625rem) !important;
    line-height: 1.25 !important;
    font-weight: 600 !important;
}
[data-testid="stMetricValue"] > div,
[data-testid="stMetricValue"] p {
    width: 100% !important;
    overflow: visible !important;
    text-overflow: clip !important;
    white-space: normal !important;
    overflow-wrap: anywhere !important;
}
[data-testid="stMetricDelta"] {
    font-size: 0.825rem !important;
    line-height: 1.35 !important;
}

.st-key-primary-price [data-testid="stMetricValue"] {
    font-size: clamp(1.75rem, 2vw, 1.875rem) !important;
    font-weight: 700 !important;
}
.st-key-primary-scores [data-testid="stMetricValue"] {
    font-size: clamp(1.75rem, 2.2vw, 2rem) !important;
    font-weight: 700 !important;
}

.st-key-primary-scores [data-testid="stMetric"] {
    min-height: 5.5rem;
}

.st-key-sina-secondary-quote [data-testid="stMetric"] {
    min-height: 4.7rem;
    padding: 0.55rem 0.7rem;
}
.st-key-sina-secondary-quote [data-testid="stMetricValue"] {
    font-size: clamp(1.05rem, 1.35vw, 1.3rem) !important;
}

.st-key-technical-summary [data-testid="stMetricValue"] {
    font-size: clamp(1.25rem, 1.55vw, 1.5rem) !important;
}

.st-key-momentum-summary [data-testid="stMetricValue"],
.st-key-momentum-summary [data-testid="stMetricValue"] > div,
.st-key-momentum-summary [data-testid="stMetricValue"] p {
    overflow: visible !important;
    text-overflow: clip !important;
    white-space: normal !important;
    overflow-wrap: normal !important;
    -webkit-line-clamp: unset !important;
}
.st-key-momentum-summary [data-testid="stMetricValue"] {
    font-size: clamp(1.05rem, 1.3vw, 1.3rem) !important;
    line-height: 1.25 !important;
}

.quick-summary {
    margin: 0.15rem 0 0.6rem;
}
.quick-summary-row {
    display: grid;
    grid-template-columns: minmax(8.5rem, max-content) 1fr;
    column-gap: 0.55rem;
    padding: 0.32rem 0;
    font-size: 0.95rem;
    line-height: 1.65;
}
.quick-summary-label {
    font-weight: 600;
}
.quick-summary-text {
    font-weight: 400;
}
[data-testid="stAlert"] {
    border-radius: var(--radius-card);
    padding: 0.9rem 1rem;
}
[data-testid="stExpander"], [data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: var(--radius-card);
}
[data-testid="stExpander"] details summary p {
    font-size: 1rem !important;
    font-weight: 600 !important;
}
[data-testid="stHorizontalBlock"] { gap: 0.9rem; }
hr {
    margin: var(--space-5) 0 !important;
    border-color: var(--border-soft) !important;
}
button, [data-testid="stFormSubmitButton"] button {
    min-height: 2.6rem;
    font-size: 0.95rem !important;
}

@media (max-width: 768px) {
    .stMainBlockContainer { padding: 1.25rem 1rem 2.25rem; }
    h2 { margin-top: 1.8rem !important; }
    [data-testid="stMetric"] {
        min-height: 5.35rem;
        padding: 0.65rem 0.75rem;
    }
    [data-testid="stHorizontalBlock"] { gap: 0.7rem; }
    .quick-summary-row {
        display: block;
        padding: 0.4rem 0;
    }
    .quick-summary-label {
        display: block;
        margin-bottom: 0.05rem;
    }
}
</style>
"""


def apply_app_theme() -> None:
    """Apply the shared typography, spacing, and card rules once per page run."""
    st.markdown(APP_CSS, unsafe_allow_html=True)
