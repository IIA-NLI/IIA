import json
import re
import httpx
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from langdetect import detect_langs
from supabase import create_client, Client
import streamlit as st


class ArchiveBackend:

    def __init__(self):
        # Initialize Supabase client using Streamlit secrets
        self.url = st.secrets["SUPABASE_URL"]
        self.key = st.secrets["SUPABASE_KEY"]
        self.supabase: Client = create_client(self.url, self.key)

    def analyze_and_extract_url(self, url: str) -> dict:
        """Scrapes URL, extracts metadata, detects languages, and translates text to English."""
        if not re.match(r"^https?://", url):
            url = "http://" + url

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = httpx.get(
                url, headers=headers, timeout=10.0, follow_redirects=True
            )
            response_code = response.status_code
            status = "success" if response_code == 200 else "error"
            status_details = f"Fetched successfully with HTTP {response_code}"
        except Exception as e:
            return {
                "url": url,
                "title": None,
                "description": None,
                "title_english": None,
                "description_english": None,
                "languages": {"error": "Failed to resolve domain"},
                "status": "failed",
                "status_details": str(e),
                "source": "manually",
                "response_code": None,
            }

        # Parse HTML elements
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.string.strip() if soup.title else ""

        desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
            "meta", attrs={"property": "og:description"}
        )
        description = desc_tag["content"].strip() if desc_tag else ""

        # Language Detection
        detected_langs = {}
        combined_text = f"{title} {description}".strip()

        if combined_text:
            try:
                predictions = detect_langs(combined_text)
                detected_langs = {p.lang: round(p.prob, 2) for p in predictions}
            except Exception:
                detected_langs = {"unknown": 1.0}
        else:
            detected_langs = {"empty": 1.0}

        # Translation Management
        title_english = title
        description_english = description
        dominant_lang = list(detected_langs.keys())[0] if detected_langs else "en"

        if dominant_lang != "en" and "empty" not in detected_langs:
            try:
                if title:
                    title_english = GoogleTranslator(
                        source="auto", target="en"
                    ).translate(title)
                if description:
                    description_english = GoogleTranslator(
                        source="auto", target="en"
                    ).translate(description)
            except Exception as e:
                status_details += f" | Translation failed: {str(e)}"

        return {
            "url": url,
            "title": title if title else None,
            "description": description if description else None,
            "title_english": title_english if title_english else None,
            "description_english": (
                description_english if description_english else None
            ),
            "languages": detected_langs,
            "status": status,
            "status_details": status_details,
            "source": "manually",
            "response_code": response_code,
        }

    def insert_domain(self, payload: dict):
        """Upserts processed metadata into the DOMAINS table."""
        return (
            self.supabase.table("DOMAINS")
            .upsert(payload, on_conflict="url")
            .execute()
        )

    def search_domains(self, query: str) -> list:
        """Queries the database looking for a keyword string across multiple columns."""
        match_str = f"%{query}%"
        response = (
            self.supabase.table("DOMAINS")
            .select("*")
            .or_(
                f"url.ilike.{match_str},title.ilike.{match_str},description.ilike.{match_str},title_english.ilike.{match_str},description_english.ilike.{match_str}"
            )
            .order("id", descending=True)
            .execute()
        )
        return response.data
