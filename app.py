import os
import time
import io
import re
import pandas as pd
import requests
import streamlit as st
from datetime import datetime

st.set_page_config(page_title="PageSpeed Outreach Demo", layout="wide")

st.title("PageSpeed Outreach Demo (Mobile)")
st.caption("Upload CSV/XLSX → Analyze via Google PageSpeed Insights API → Export results (XLSX + CSV for outreach)")

API_KEY = os.getenv("PAGESPEED_API_KEY", "").strip()
if not API_KEY:
    st.warning("Missing PAGESPEED_API_KEY. Add it in Streamlit Cloud → App settings → Secrets.")
    st.stop()

CANON = ["website", "email", "name", "company", "linkedin"]

SYNONYMS = {
    "website": [
        "website", "url", "site", "domain", "company url", "company website", "web", "homepage", "home page",
        "company_url", "company website url", "companyurl"
    ],
    "email": ["email", "e-mail", "mail", "work email", "email address", "emailaddress"],
    "name": ["name", "first name", "firstname", "full name", "contact name", "contact", "person name"],
    "company": ["company", "company name", "organization", "organisation", "business", "org", "account"],
    "linkedin": ["linkedin", "linkedin url", "linkedin profile", "profile", "user social", "social"]
}

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())

def looks_like_url(v: str) -> bool:
    v = (v or "").strip().lower()
    return v.startswith("http://") or v.startswith("https://") or "www." in v

def looks_like_email(v: str) -> bool:
    v = (v or "").strip()
    return "@" in v and "." in v and " " not in v

def looks_like_linkedin(v: str) -> bool:
    v = (v or "").strip().lower()
    return "linkedin.com" in v

def auto_map_columns(df: pd.DataFrame) -> dict:
    """Return mapping: canon_key -> original_column or None."""
    cols = list(df.columns)
    cols_norm = {c: norm(c) for c in cols}
    mapping = {k: None for k in CANON}

    # 1) Header synonyms
    for canon_key in CANON:
        candidates = set(SYNONYMS.get(canon_key, []))
        for orig, c_norm in cols_norm.items():
            if c_norm in candidates:
                mapping[canon_key] = orig
                break

    # 2) Heuristic content-based fallback (lightweight)
    sample = df.head(30)

    # website
    if mapping["website"] is None:
        best_col = None
        best_score = 0
        for c in cols:
            vals = sample[c].astype(str).fillna("").tolist()
            score = sum(1 for v in vals if looks_like_url(v))
            if score > best_score:
                best_score = score
                best_col = c
        if best_score >= 3:  # at least some URLs
            mapping["website"] = best_col

    # email
    if mapping["email"] is None:
        best_col = None
        best_score = 0
        for c in cols:
            vals = sample[c].astype(str).fillna("").tolist()
            score = sum(1 for v in vals if looks_like_email(v))
            if score > best_score:
                best_score = score
                best_col = c
        if best_score >= 3:
            mapping["email"] = best_col

    # linkedin
    if mapping["linkedin"] is None:
        for c in cols:
            vals = sample[c].astype(str).fillna("").tolist()
            if sum(1 for v in vals if looks_like_linkedin(v)) >= 2:
                mapping["linkedin"] = c
                break

    return mapping

def get_pagespeed(url: str, api_key: str) -> dict:
    endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {
        "url": url,
        "strategy": "mobile",
        "category": ["performance", "accessibility", "best-practices", "seo"],
        "key": api_key,
    }
    r = requests.get(endpoint, params=params, timeout=90)
    r.raise_for_status()
    data = r.json()
    cats = data.get("lighthouseResult", {}).get("categories", {})

    def score(cat_key: str):
        v = cats.get(cat_key, {}).get("score", None)
        return None if v is None else int(round(v * 100))

    return {
        "mobile_performance": score("performance"),
        "accessibility": score("accessibility"),
        "best_practices": score("best-practices"),
        "seo": score("seo"),
    }

def decision_and_email(perf: int | None, name: str | None):
    # If score missing, treat as error/skip
    if perf is None:
        return ("skip", "", "", "Score not available")

    # Decision
    if perf >= 91:
        return ("skip", "", "", "Performance is 90+ (no outreach)")

    # greeting fallback
    greet_name = (name or "").strip()
    if not greet_name:
        greet = "Hi there,"
    else:
        greet = f"Hi {greet_name},"

    # Template buckets
    if perf <= 50:
        subject = "Quick note about your website performance"
        body = (
            f"{greet}\n\n"
            "I reviewed your website using Google PageSpeed Insights and noticed that the mobile performance score is extremely low.\n"
            "This negatively affects search visibility, organic traffic, and significantly increases advertising costs if you are running paid campaigns.\n\n"
            "We can fix this within 2–3 days, with an estimated cost of $400–600.\n"
            "If this is interesting for you, just reply to this email.\n\n"
            "Best regards,\n"
            "Roman Sidorin\n"
            "DITS.AGENCY"
        )
        bucket = "0-50"
    elif 51 <= perf <= 75:
        subject = "Website performance improvement opportunity"
        body = (
            f"{greet}\n\n"
            "I reviewed your website via Google PageSpeed Insights and noticed that the mobile performance is quite weak.\n"
            "This can negatively impact search visibility, organic traffic, and increase advertising costs when running paid campaigns.\n\n"
            "We can improve this within 2–3 days, with a budget of $400–600.\n"
            "If you’re interested, simply reply to this email.\n\n"
            "Best regards,\n"
            "Roman Sidorin\n"
            "DITS.AGENCY"
        )
        bucket = "51-75"
    else:  # 76–90
        subject = "Reaching 90+ PageSpeed score for your website"
        body = (
            f"{greet}\n\n"
            "Your website already shows a fairly good mobile performance score.\n"
            "However, if your goal is to reach 90+, we can help you achieve this.\n\n"
            "In most cases, this takes 2–3 days and costs $400–800.\n"
            "If this sounds interesting, just reply to this email.\n\n"
            "Best regards,\n"
            "Roman Sidorin\n"
            "DITS.AGENCY"
        )
        bucket = "76-90"

    note = f"Mobile performance {perf}. Offer depends on bucket {bucket}."
    return ("send", subject, body, note)

def read_any(uploaded) -> pd.DataFrame:
    name = (uploaded.name or "").lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded)
    # xlsx/xls
    return pd.read_excel(uploaded)

st.subheader("1) Upload file")
st.write("Supported: **CSV, XLSX**. We'll auto-detect columns and you can adjust mapping if needed.")
uploaded = st.file_uploader("Upload CSV/XLSX", type=["csv", "xlsx", "xls"])

rate_sleep = st.slider("Delay between checks (seconds)", 0.0, 3.0, 1.0, 0.5)
max_rows = st.number_input("Max rows to process", min_value=1, max_value=5000, value=200, step=50)

if uploaded:
    df_raw = read_any(uploaded)
    # keep original column names, but also create a normalized view for matching
    df_raw = df_raw.head(int(max_rows)).copy()

    st.subheader("2) Preview (raw)")
    st.dataframe(df_raw.head(50), use_container_width=True)

    # Auto mapping
    mapping_auto = auto_map_columns(df_raw)

    st.subheader("3) Column mapping")
    st.write("We auto-detected columns. If something is wrong, select the right column manually.")
    cols = ["(none)"] + list(df_raw.columns)

    def default_index(col_name: str | None):
        if col_name and col_name in df_raw.columns:
            return cols.index(col_name)
        return 0

    mapping_ui = {}
    mapping_ui["website"] = st.selectbox("website (required)", cols, index=default_index(mapping_auto.get("website")))
    mapping_ui["email"] = st.selectbox("email (optional)", cols, index=default_index(mapping_auto.get("email")))
    mapping_ui["name"] = st.selectbox("name (optional)", cols, index=default_index(mapping_auto.get("name")))
    mapping_ui["company"] = st.selectbox("company (optional)", cols, index=default_index(mapping_auto.get("company")))
    mapping_ui["linkedin"] = st.selectbox("linkedin (optional)", cols, index=default_index(mapping_auto.get("linkedin")))

    if mapping_ui["website"] == "(none)":
        st.error("Please select a column for **website** (required).")
        st.stop()

    finalized = {k: (None if v == "(none)" else v) for k, v in mapping_ui.items()}

    # Build canonical df
    df = pd.DataFrame()
    df["website"] = df_raw[finalized["website"]].astype(str) if finalized["website"] else ""
    df["email"] = df_raw[finalized["email"]].astype(str) if finalized["email"] else ""
    df["name"] = df_raw[finalized["name"]].astype(str) if finalized["name"] else ""
    df["company"] = df_raw[finalized["company"]].astype(str) if finalized["company"] else ""
    df["linkedin"] = df_raw[finalized["linkedin"]].astype(str) if finalized["linkedin"] else ""

    for c in df.columns:
        df[c] = df[c].fillna("").astype(str).str.strip()

    st.subheader("4) Preview (normalized)")
    st.dataframe(df.head(50), use_container_width=True)

    if st.button("Run analysis"):
        results = []
        progress = st.progress(0)
        status = st.empty()
        total = len(df)

        for idx, row in df.iterrows():
            url = str(row.get("website", "")).strip()
            name_val = str(row.get("name", "")).strip() or None

            status.write(f"Analyzing {idx+1}/{total}: {url}")
            ts = datetime.utcnow().isoformat()

            try:
                scores = get_pagespeed(url, API_KEY)
                perf = scores["mobile_performance"]
                decision, subj, body, note = decision_and_email(perf, name_val)
                err = ""
            except Exception as e:
                scores = {"mobile_performance": None, "accessibility": None, "best_practices": None, "seo": None}
                decision, subj, body, note = ("skip", "", "", "Error during analysis")
                err = str(e)[:300]

            results.append({
                "website": url,
                "email": row.get("email", ""),
                "name": row.get("name", ""),
                "company": row.get("company", ""),
                "linkedin": row.get("linkedin", ""),
                **scores,
                "decision": decision,
                "email_subject": subj,
                "email_body": body,
                "audit_note": note,
                "timestamp_utc": ts,
                "error": err,
            })

            progress.progress(min(1.0, (len(results) / total)))
            if rate_sleep > 0:
                time.sleep(rate_sleep)

        res = pd.DataFrame(results)

        st.subheader("5) Results (full)")
        st.dataframe(res, use_container_width=True)

        # Export #1: results.xlsx (full)
        xlsx_buf = io.BytesIO()
        with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as writer:
            res.to_excel(writer, index=False, sheet_name="results")
        xlsx_buf.seek(0)

        # Export #2: snov_import.csv (only send)
        snov = res[res["decision"] == "send"].copy()
        snov_out = pd.DataFrame({
            "email": snov["email"],
            "first_name": snov["name"],
            "company": snov["company"],
            "website": snov["website"],
            "linkedin": snov["linkedin"],
            "mobile_performance": snov["mobile_performance"],
            "email_subject": snov["email_subject"],
            "email_body": snov["email_body"],
            "audit_note": snov["audit_note"],
        })

        csv_buf = io.BytesIO()
        snov_out.to_csv(csv_buf, index=False)
        csv_buf.seek(0)

        st.download_button("Download results.xlsx", data=xlsx_buf, file_name="results.xlsx")
        st.download_button("Download snov_import.csv", data=csv_buf, file_name="snov_import.csv")

        st.success("Done.")
