import json
import re
import httpx
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from langdetect import detect_langs
from supabase import create_client, Client
import streamlit as st
from urllib.parse import urlparse

try:
    from googleapiclient.discovery import build
except Exception:
    build = None


class ArchiveBackend:

    def __init__(self):
        # Initialize Supabase client using Streamlit secrets
        self.url = st.secrets["SUPABASE_URL"]
        self.key = st.secrets["SUPABASE_KEY"]
        self.supabase: Client = create_client(self.url, self.key)

    # =========================================================================
    # DOMAINS TABLE MANAGEMENT
    # =========================================================================

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
            status = "Approved" if response_code == 200 else "Not sure"
            status_details = "Manually added"
        except Exception as e:
            return {
                "url": url,
                "title": None,
                "description": None,
                "title_english": None,
                "description_english": None,
                "languages": {"error": "Failed to resolve domain"},
                "status": "Not relevant",
                "status_details": "Manually added",
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
            except Exception:
                pass  # Keep status_details safely set to "Manually added"

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

    def google_search(self, query: str, num_results: int = 100, language: str = "en") -> list:
        """Fetch up to `num_results` results from Google CSE.

        Returns a list of URL strings. Requires `cse_key` and `cse_id` in Streamlit secrets.
        """
        api_key = st.secrets.get("cse_key")
        cse_id = st.secrets.get("cse_id")

        lang_for_hl = (language or "en")
        lang_for_lr = (language.split("-")[0].lower() if language else "en")
        lr_param = f"lang_{lang_for_lr}" if len(lang_for_lr) == 2 else None

        if build is None:
            raise RuntimeError("googleapiclient.discovery.build is not available in the environment")

        service = build("customsearch", "v1", developerKey=api_key)

        target = min(int(num_results), 100)
        all_results = []
        start_index = 1

        while len(all_results) < target and start_index <= 91:
            page_num = min(10, target - len(all_results))

            req = {
                "q": query,
                "cx": cse_id,
                "num": page_num,
                "start": start_index,
                "hl": lang_for_hl,
            }
            if lr_param:
                req["lr"] = lr_param

            results = service.cse().list(**req).execute()
            items = results.get("items", [])
            if not items:
                break

            for item in items:
                link = item.get("link")
                if link:
                    all_results.append(link)
                    if len(all_results) >= target:
                        break

            start_index += 10

            total_avail_str = results.get("searchInformation", {}).get("totalResults", "0")
            try:
                total_avail = int(total_avail_str)
                if start_index > total_avail:
                    break
            except ValueError:
                pass

        return all_results

    def evaluate_payload(self, payload: dict, good_keywords: list | None = None) -> dict:
        """Apply auto-evaluation rules to a scraped payload and set status/status_details.

        Rules implemented:
        - Hostname endswith `.il` => Approved
        - Detected languages include Hebrew (`he`) => Approved
        - Any `good_keywords` found in title/description (orig or english) => Approved
        Otherwise status is `Not sure`.
        """
        approved = False

        url = payload.get("url") or ""
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname.endswith(".il"):
            approved = True

        languages = payload.get("languages") or {}
        if any(str(l).lower().startswith("he") for l in languages.keys()):
            approved = True

        if good_keywords:
            text_fields = " ".join([str(payload.get(k) or "") for k in ("title", "description", "title_english", "description_english")]).lower()
            for kw in good_keywords:
                if kw.lower() in text_fields:
                    approved = True
                    break

        # Determine status details priority:
        # 1) good keywords found -> "Found X good keywords"
        # 2) .il domain -> ".il suffix"
        # 3) Hebrew detected -> "Hebrew"
        kw_count = 0
        if good_keywords:
            text_fields = " ".join([str(payload.get(k) or "") for k in ("title", "description", "title_english", "description_english")]).lower()
            for kw in good_keywords:
                if kw.lower() in text_fields:
                    kw_count += 1

        if hostname.endswith(".il"):
            payload["status_details"] = ".il suffix"
        elif any(str(l).lower().startswith("he") for l in languages.keys()):
            payload["status_details"] = "Hebrew"
        elif kw_count > 0:
            payload["status_details"] = f"Found {kw_count} good keywords"
        else:
            payload["status_details"] = "Approved by user"

        payload["status"] = "Approved" if approved else "Not sure"
        return payload

    def domain_exists(self, domain_url: str) -> bool:
        """Check whether a domain already exists in `DOMAINS`.

        Accepts a normalized domain netloc (no scheme, no leading www), e.g. `example.com`.
        This will check common stored variants (with http/https and with/without www).
        """
        try:
            # Prepare variants to check against stored `url` column
            variants = [
                domain_url,
                f"http://{domain_url}",
                f"https://{domain_url}",
                f"http://www.{domain_url}",
                f"https://www.{domain_url}",
                f"www.{domain_url}",
            ]

            for v in variants:
                resp = (
                    self.supabase.table("DOMAINS")
                    .select("id")
                    .eq("url", v)
                    .limit(1)
                    .execute()
                )
                if resp and getattr(resp, 'data', None):
                    if resp.data:
                        return True

            return False
        except Exception:
            return False

    def insert_domain(self, payload: dict):
        """Upserts processed metadata into the DOMAINS table."""
        return (
            self.supabase.table("DOMAINS")
            .upsert(payload, on_conflict="url")
            .execute()
        )

    def get_all_domains(self) -> list:
        """Fetches all records from the DOMAINS database table."""
        response = (
            self.supabase.table("DOMAINS")
            .select("*")
            .order("id", desc=True)
            .execute()
        )
        return response.data

    def search_domains(self, query: str) -> list:
        """Queries the database looking for a keyword string across multiple columns."""
        match_str = f"%{query}%"
        response = (
            self.supabase.table("DOMAINS")
            .select("*")
            .or_(
                f"url.ilike.{match_str},title.ilike.{match_str},description.ilike.{match_str},title_english.ilike.{match_str},description_english.ilike.{match_str}"
            )
            .order("id", desc=True)
            .execute()
        )
        return response.data

    def advanced_search_domains(self, filters: dict) -> list:
        """Executes a compound multi-column advanced filter search query."""
        query_builder = self.supabase.table("DOMAINS").select("*")

        # Handle text search constraints
        if filters.get("text_query"):
            match_str = f"%{filters['text_query']}%"
            query_builder = query_builder.or_(
                f"url.ilike.{match_str},title.ilike.{match_str},description.ilike.{match_str},title_english.ilike.{match_str},description_english.ilike.{match_str}"
            )

        # Handle relational filter tags
        if filters.get("status"):
            query_builder = query_builder.eq("status", filters["status"])
            
        if filters.get("response_code"):
            query_builder = query_builder.eq("response_code", filters["response_code"])

        response = query_builder.order("id", desc=True).execute()
        data = response.data

        # Post-filter languages dictionary structure since Supabase stores it as JSONB
        if filters.get("language") and data:
            target_lang = filters["language"].lower()
            data = [
                row for row in data 
                if isinstance(row.get("languages"), dict) and target_lang in row["languages"]
            ]

        return data

    # =========================================================================
    # KEYWORDS TABLE MANAGEMENT
    # =========================================================================

    def analyze_keywords_before_saving(self, words: list) -> list:
        """
        Splits text input and runs language detection on each word token.
        Returns an analyzed structured array for UI modification.
        """
        analyzed_list = []
        for word in words:
            word_clean = word.strip()
            if not word_clean:
                continue

            try:
                predictions = detect_langs(word_clean)
                detected_langs = {p.lang: round(p.prob, 2) for p in predictions}
            except Exception:
                detected_langs = {"unknown": 1.0}

            analyzed_list.append({
                "word": word_clean,
                "languages_list": list(detected_langs.keys())
            })
        return analyzed_list

    def insert_final_keywords(self, payloads: list) -> dict:
        """Saves a prepared and finalized payload batch directly to the database."""
        if not payloads:
            return {"success": False, "message": "No data provided."}

        try:
            response = (
                self.supabase.table("KEYWORDS")
                .upsert(payloads, on_conflict="word")
                .execute()
            )
            return {"success": True, "count": len(payloads)}
        except Exception as e:
            raise Exception(f"Database insertion failed: {str(e)}")

    def get_all_keywords(self) -> list:
        """Fetches all keywords from the database sorted by generation date."""
        response = (
            self.supabase.table("KEYWORDS")
            .select("*")
            .order("id", desc=True)
            .execute()
        )
        return response.data

    def delete_keyword(self, keyword_id: int):
        """Removes a keyword signature index from the KEYWORDS table by its unique ID."""
        return (
            self.supabase.table("KEYWORDS")
            .delete()
            .eq("id", keyword_id)
            .execute()
        )