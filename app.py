import os
import time
import io
import pandas as pd
import requests
import streamlit as st
from datetime import datetime

st.set_page_config(page_title="PageSpeed Outreach Demo", layout="wide")

st.title("PageSpeed Outreach Demo (Mobile)")
st.caption("Upload CSV → Analyze via Google PageSpeed Insights API → Export results (XLSX + CSV for outreach)")

API_KEY = os.getenv("PAGESPEED_API_KEY", "").strip()

if not API_KEY:
    st.warning("Missing PAGESPEED_API_KEY. Add it in Streamlit Cloud → App settings → Secrets / Environment variables.")
    st.stop()

def get_pagespeed(url: str, api_key: str) -> dict:
    endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {
        "url": url,
        "strategy": "mobile",
        "category": ["performance", "accessibility", "best-practices", "seo"],
        "key": api_key,
    }
    r = requests.get(endpoint, params=params, timeout=60)
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

    # Template buckets
    if perf <= 50:
        subject = "Quick note about your website performance"
        body = (
            f"Hi{f' {name}' if name else ''},\n\n"
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
            f"Hi{f' {name}' if name else ''},\n\n"
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
            f"Hi{f' {name}' if name else ''},\n\n"
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

st.subheader("1) Upload CSV")
st.write("Required columns: **website**. Optional: **email**, **name**, **company**.")
uploaded = st.file_uploader("Upload CSV", type=["csv"])

rate_sleep = st.slider("Delay between checks (seconds)", 0.0, 3.0, 1.0, 0.5)
max_rows = st.number_input("Max rows to process", min_value=1, max_value=5000, value=200, step=50)

if uploaded:
    df = pd.read_csv(uploaded)
    df.columns = [c.strip().lower() for c in df.columns]

    if "website" not in df.columns:
        st.error("CSV must contain a 'website' column.")
        st.stop()

    df = df.head(int(max_rows)).copy()
    if "email" not in df.columns:
        df["email"] = ""
    if "name" not in df.columns:
        df["name"] = ""
    if "company" not in df.columns:
        df["company"] = ""

    st.subheader("2) Preview")
    st.dataframe(df, use_container_width=True)

    if st.button("Run analysis"):
        results = []
        progress = st.progress(0)
        status = st.empty()
        total = len(df)

        for i, row in df.iterrows():
            url = str(row.get("website", "")).strip()
            name = str(row.get("name", "")).strip() or None

            status.write(f"Analyzing {i+1}/{total}: {url}")
            ts = datetime.utcnow().isoformat()

            try:
                scores = get_pagespeed(url, API_KEY)
                perf = scores["mobile_performance"]
                decision, subj, body, note = decision_and_email(perf, name)
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

        st.subheader("3) Results")
        st.dataframe(res, use_container_width=True)

        # Export XLSX
        xlsx_buf = io.BytesIO()
        with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as writer:
            res.to_excel(writer, index=False, sheet_name="results")
        xlsx_buf.seek(0)

        # Export snov_import CSV (simple universal)
        snov = res.copy()
        # Keep only leads to send
        snov = snov[snov["decision"] == "send"].copy()
        # Snov-friendly columns (generic)
        snov_out = pd.DataFrame({
            "email": snov["email"],
            "first_name": snov["name"],
            "website": snov["website"],
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
