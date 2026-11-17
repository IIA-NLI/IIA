import json
import re
import time
import httpx
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from langdetect import detect_langs
from supabase import create_client, Client
import streamlit as st
from urllib.parse import urlparse
from datetime import datetime
import pytz

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

try:
    from googleapiclient.discovery import build
except Exception:
    build = None


# A Hebrew word: 2 or more Hebrew letters in a row (niqqud allowed between them).
# Includes the Yiddish ligatures, so Yiddish and Ladino text written in Hebrew script counts too.
HEBREW_WORD = re.compile(r"(?:[א-תװ-ײ][֑-ׇ]*){2,}")


# Shared-hosting platforms: far too large to archive as a whole, so the main domain is
# "Not relevant". Their subdomains (individual sites) are judged on their own by the keyword
# process: they never inherit a status from the platform, and the platform never inherits one.
# To add a platform, add its domain to this set.
SHARED_HOSTING_DOMAINS = {
    "wordpress.com", "blogspot.com", "github.io", "wixsite.com", "medium.com",
    "substack.com", "tumblr.com", "weebly.com", "squarespace.com", "netlify.app",
    "vercel.app", "herokuapp.com", "web.app", "pages.dev", "glitch.me",
}

_IP_ADDRESS = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


class ArchiveBackend:

    def __init__(self):
        # Initialize Supabase client using Streamlit secrets
        self.url = st.secrets["SUPABASE_URL"]
        self.key = st.secrets["SUPABASE_KEY"]
        self.supabase: Client = create_client(self.url, self.key)

    # =========================================================================
    # TIMEZONE & VALIDATION HELPERS
    # =========================================================================

    def _now_jerusalem_iso(self) -> str:
        """Return current time as ISO string strictly in Asia/Jerusalem timezone."""
        try:
            if ZoneInfo:
                tz = ZoneInfo("Asia/Jerusalem")
                return datetime.now(tz).isoformat()
        except Exception:
            pass

        try:
            tz = pytz.timezone("Asia/Jerusalem")
            return datetime.now(tz).isoformat()
        except Exception:
            # Fallback for systems missing external timezone libraries
            # Manual offset calculation can go here if needed, but standardizing on a explicit string marker
            return datetime.now().isoformat()

    def is_valid_url_format(self, url: str) -> bool:
        """
        Validates if the incoming string conforms to a standard URL layout.
        Strictly forbids whitespaces or obvious illegal special characters.
        """
        clean_url = url.strip()
        if not clean_url or " " in clean_url:
            return False
        
        # Enforce basic scheme prefix check to pass it to urlparse safely
        test_url = clean_url
        if not test_url.lower().startswith("http://") and not test_url.lower().startswith("https://"):
            test_url = "http://" + test_url

        try:
            parsed = urlparse(test_url)
            # URL must have a valid hostname/netloc and cannot contain control symbols or spaces
            if not parsed.netloc or not re.match(r"^[a-zA-Z0-9.:\-_]+$", parsed.netloc):
                return False
            return True
        except Exception:
            return False

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
        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
            "meta", attrs={"property": "og:description"}
        )
        description = desc_tag["content"].strip() if desc_tag and desc_tag.get("content") else ""

        # Discard placeholder metadata values that are not meaningful.
        invalid_placeholders = {"just a moment..."}
        if title and title.strip().lower() in invalid_placeholders:
            title = ""
        if description and description.strip().lower() in invalid_placeholders:
            description = ""

        # Language Detection
        detected_langs = []
        combined_text = f"{title} {description}".strip()

        def _extract_page_language(soup_obj: BeautifulSoup) -> str | None:
            html_tag = soup_obj.find("html")
            if html_tag:
                lang_value = html_tag.get("lang") or html_tag.get("xml:lang")
                if lang_value:
                    return lang_value.strip()

            meta_lang = soup_obj.find("meta", attrs={"http-equiv": "Content-Language"})
            if meta_lang and meta_lang.get("content"):
                return meta_lang["content"].strip()

            meta_lang = soup_obj.find("meta", attrs={"name": "language"})
            if meta_lang and meta_lang.get("content"):
                return meta_lang["content"].strip()

            return None

        page_lang = _extract_page_language(soup)
        if page_lang:
            lang_code = page_lang.lower().strip()
            if ";" in lang_code:
                lang_code = lang_code.split(";", 1)[0].strip()
            if "," in lang_code:
                lang_code = lang_code.split(",", 1)[0].strip()
            if lang_code:
                normalized = self._lang_name_to_code(lang_code) if not re.match(r"^[a-z]{2}(?:-[a-z]{2})?$", lang_code) else lang_code
                detected_langs = [self._lang_code_to_name(normalized)]

        if not detected_langs:
            if combined_text:
                try:
                    predictions = detect_langs(combined_text)
                    filtered = [p for p in predictions if getattr(p, "prob", 0.0) > 0.5]
                    if filtered:
                        detected_langs = [self._lang_code_to_name(p.lang) for p in filtered]
                    else:
                        detected_langs = ["Unknown"]
                except Exception:
                    detected_langs = ["Unknown"]
            else:
                detected_langs = []

        # Translation Management
        # Title and description are translated separately; a failure is retried once and
        # is flagged (translation_failed) instead of being ignored silently. Text that
        # already looks English is not sent to the translator.
        title_english = title
        description_english = description
        translation_failed = False

        if combined_text and not self._looks_english(combined_text):
            if title:
                title_english, ok = self._translate_to_english(title)
                translation_failed = translation_failed or not ok
            if description:
                description_english, ok = self._translate_to_english(description)
                translation_failed = translation_failed or not ok
            self._record_translation_result(translation_failed)

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
            "translation_failed": translation_failed,
        }

    def _looks_english(self, text: str) -> bool:
        """True when the language detector is confident that the text is English."""
        try:
            predictions = detect_langs(text)
            return bool(predictions) and predictions[0].lang == "en" and predictions[0].prob > 0.9
        except Exception:
            return False

    def _translate_to_english(self, text: str) -> tuple:
        """Translate text to English. Returns (text, ok); on failure returns the original text and ok=False.

        Retries once. While a search is running, translation is skipped for the rest of the
        search after 3 pages in a row failed to translate, so an outage cannot slow it down.
        """
        in_search = getattr(self, "_translation_breaker_active", False)
        if in_search and getattr(self, "_translation_failures", 0) >= 3:
            return text, False

        for attempt in range(2):
            try:
                translated = GoogleTranslator(source="auto", target="en").translate(text)
                if translated:
                    return translated, True
            except Exception:
                pass
            if attempt == 0:
                time.sleep(1)
        return text, False

    def _record_translation_result(self, failed: bool) -> None:
        """Count consecutive pages whose translation failed (used by the per-search breaker)."""
        if getattr(self, "_translation_breaker_active", False):
            self._translation_failures = (getattr(self, "_translation_failures", 0) + 1) if failed else 0

    def _note_translation_failure(self, payload: dict) -> dict:
        """Add 'translation failed' to the status details of a page whose translation failed."""
        if payload.get("translation_failed"):
            details = payload.get("status_details") or ""
            if "translation failed" not in details:
                payload["status_details"] = f"{details}; translation failed" if details else "translation failed"
        return payload

    def normalize_manual_url(self, url: str) -> tuple[str, str, bool, str]:
        """Normalize a URL and return a consistent domain key for manual ingestion.

        Returns:
            normalized_url: URL with a scheme if missing
            domain_key: host normalized without leading www.
            has_extra_path: True when path/query/fragment exist beyond a bare domain
            trimmed_url: scheme://netloc domain root
        """
        normalized_url = url.strip()
        if not normalized_url.lower().startswith("http://") and not normalized_url.lower().startswith("https://"):
            normalized_url = "http://" + normalized_url

        parsed = urlparse(normalized_url)
        netloc = (parsed.netloc or "").lower()
        if netloc.startswith("www."):
            domain_key = netloc[4:]
        else:
            domain_key = netloc

        path = parsed.path.rstrip("/")
        has_extra_path = bool(path and path != "") or bool(parsed.query) or bool(parsed.fragment)
        trimmed_url = f"{parsed.scheme}://{parsed.netloc}"
        return normalized_url, domain_key, has_extra_path, trimmed_url

    def _normalize_site_domain(self, site_domain: str) -> str:
        """Normalize a site domain value for use inside search queries."""
        trimmed = site_domain.strip()
        if not trimmed:
            return ""
        if trimmed.startswith("http://") or trimmed.startswith("https://"):
            parsed = urlparse(trimmed)
            normalized = parsed.netloc or trimmed
        else:
            normalized = trimmed
        if normalized.startswith("www."):
            normalized = normalized[4:]
        return normalized

    def prepare_manual_url(self, url: str) -> dict:
        """Normalize a manual URL and inspect whether its domain already exists."""
        normalized_url, domain_key, has_extra_path, trimmed_url = self.normalize_manual_url(url)
        return {
            "normalized_url": normalized_url,
            "domain_key": domain_key,
            "has_extra_path": has_extra_path,
            "trimmed_url": trimmed_url,
            "domain_exists": self.domain_exists(domain_key),
        }

    # Language mapping helpers
    def _lang_code_to_name(self, code: str) -> str:
        if not code:
            return "Unknown"
        c = code.lower()
        mapping = {
            "af": "Afrikaans", "sq": "Albanian", "am": "Amharic", "ar": "Arabic", "hy": "Armenian",
            "az": "Azerbaijani", "eu": "Basque", "be": "Belarusian", "bn": "Bengali", "bs": "Bosnian",
            "bg": "Bulgarian", "ca": "Catalan", "ceb": "Cebuano", "ny": "Chichewa", "zh-cn": "Chinese (Simplified)",
            "zh-tw": "Chinese (Traditional)", "zh": "Chinese", "co": "Corsican", "hr": "Croatian", "cs": "Czech",
            "da": "Danish", "nl": "Dutch", "en": "English", "eo": "Esperanto", "et": "Estonian", "tl": "Filipino",
            "fi": "Finnish", "fr": "French", "fy": "Frisian", "gl": "Galician", "ka": "Georgian", "de": "German",
            "el": "Greek", "gu": "Gujarati", "ht": "Haitian Creole", "ha": "Hausa", "haw": "Hawaiian",
            "iw": "Hebrew", "he": "Hebrew", "hi": "Hindi", "hmn": "Hmong", "hu": "Hungarian", "is": "Icelandic",
            "ig": "Igbo", "id": "Indonesian", "ga": "Irish", "it": "Italian", "ja": "Japanese", "jw": "Javanese",
            "kn": "Kannada", "kk": "Kazakh", "km": "Khmer", "ko": "Korean", "ku": "Kurdish", "ky": "Kyrgyz",
            "lo": "Lao", "la": "Latin", "lv": "Latvian", "lt": "Lithuanian", "lb": "Luxembourgish", "mk": "Macedonian",
            "mg": "Malagasy", "ms": "Malay", "ml": "Malayalam", "mt": "Maltese", "mi": "Maori", "mr": "Marathi",
            "mn": "Mongolian", "my": "Myanmar (Burmese)", "ne": "Nepali", "no": "Norwegian", "ps": "Pashto",
            "fa": "Persian", "pl": "Polish", "pt": "Portuguese", "pt-br": "Portuguese - Brazil", "pt-pt": "Portuguese - Portugal",
            "pa": "Punjabi", "ro": "Romanian", "ru": "Russian", "sm": "Samoan", "gd": "Scots Gaelic", "sr": "Serbian",
            "st": "Sesotho", "sn": "Shona", "sd": "Sindhi", "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian",
            "so": "Somali", "es": "Spanish", "es-419": "Spanish - Latin America", "es-es": "Spanish - Spain",
            "su": "Sundanese", "sw": "Swahili", "sv": "Swedish", "tg": "Tajik", "ta": "Tamil", "te": "Telugu",
            "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese",
            "cy": "Welsh", "xh": "Xhosa", "yi": "Yiddish", "yo": "Yoruba", "zu": "Zulu",
        }
        if c in mapping:
            return mapping[c]
        for k, v in mapping.items():
            if c.startswith(k):
                return v
        return code

    def _lang_name_to_code(self, name: str) -> str:
        if not name:
            return "en"
        n = name.strip().lower()
        rev = {
            "afrikaans": "af", "albanian": "sq", "amharic": "am", "arabic": "ar", "armenian": "hy",
            "azerbaijani": "az", "basque": "eu", "belarusian": "be", "bengali": "bn", "bosnian": "bs",
            "bulgarian": "bg", "catalan": "ca", "cebuano": "ceb", "chichewa": "ny", "chinese": "zh",
            "chinese (simplified)": "zh-cn", "chinese (traditional)": "zh-tw", "corsican": "co", "croatian": "hr",
            "czech": "cs", "danish": "da", "dutch": "nl", "english": "en", "esperanto": "eo", "estonian": "et",
            "filipino": "tl", "finnish": "fi", "french": "fr", "frisian": "fy", "galician": "gl", "georgian": "ka",
            "german": "de", "greek": "el", "gujarati": "gu", "haitian creole": "ht", "hausa": "ha", "hawaiian": "haw",
            "hebrew": "he", "hindi": "hi", "hmong": "hmn", "hungarian": "hu", "icelandic": "is", "igbo": "ig",
            "indonesian": "id", "irish": "ga", "italian": "it", "japanese": "ja", "javanese": "jw", "kannada": "kn",
            "kazakh": "kk", "khmer": "km", "ko": "Korean", "kurdish": "ku", "ky": "Kyrgyz", "lao": "lo",
            "latin": "la", "lv": "lv", "lithuanian": "lt", "luxembourgish": "lb", "macedonian": "mk",
            "malagasy": "mg", "malay": "ms", "malayalam": "ml", "maltese": "mt", "maori": "mi", "marathi": "mr",
            "mongolian": "mn", "myanmar (burmese)": "my", "nepali": "ne", "norwegian": "no", "pashto": "ps",
            "persian": "fa", "polish": "pl", "portuguese": "pt", "portuguese - brazil": "pt-br", "portuguese - portugal": "pt-pt",
            "punjabi": "pa", "romanian": "ro", "russian": "ru", "samoan": "sm", "scots gaelic": "gd", "serbian": "sr",
            "sesotho": "st", "shona": "sn", "sindhi": "sd", "sinhala": "si", "sk": "sk", "slovenian": "sl",
            "somali": "so", "spanish": "es", "spanish - latin america": "es-419", "spanish - spain": "es-es",
            "sundanese": "su", "sw": "sw", "swedish": "sv", "tajik": "tg", "tamil": "ta", "telugu": "te",
            "thai": "th", "turkish": "tr", "ukrainian": "uk", "urdu": "ur", "uzbek": "uz", "vietnamese": "vi",
            "welsh": "cy", "xh": "xh", "yiddish": "yi", "yoruba": "yo", "zulu": "zu",
        }
        return rev.get(n, "en")

    def google_search(
        self,
        query: str,
        num_results: int = 100,
        language: str = "en",
        site_domain: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list:
        """Fetch up to `num_results` results from Google CSE with optional site/date filters."""
        api_key = st.secrets.get("cse_key")
        cse_id = st.secrets.get("cse_id")

        if site_domain:
            normalized_site = self._normalize_site_domain(site_domain)
            if normalized_site:
                query = f"site:{normalized_site} {query}"

        if date_from:
            query = f"{query} after:{date_from}"
        if date_to:
            query = f"{query} before:{date_to}"

        if not language:
            language_code = "en"
        elif re.match(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,4})?$", language.strip()):
            # Already a language code such as "he", "pt-BR" or "es-419"
            language_code = language.strip()
        else:
            language_code = self._lang_name_to_code(language)

        lang_for_hl = (language_code or "en")
        lang_for_lr = (language_code.split("-")[0].lower() if language_code else "en")
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
    
    def evaluate_payload(self, payload: dict, good_keywords: list | None = None, bad_keywords: list | None = None) -> dict:
        """Apply auto-evaluation rules to a scraped payload and set status/status_details."""
        good_keywords = good_keywords or []
        bad_keywords = bad_keywords or []

        # 1. IL
        url = payload.get("url") or ""
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        if hostname.endswith(".il"):
            payload["status"] = "Approved"
            payload["status_details"] = ".il suffix"

        else:
            # 2. Hebrew
            languages = payload.get("languages") or []
            if isinstance(languages, dict):
                language_items = list(languages.keys())
            else:
                language_items = languages
            hebrew_detected = any("hebrew" in str(l).lower() for l in language_items)

            if hebrew_detected:
                payload["status"] = "Approved"
                payload["status_details"] = "Hebrew"

            else:
                # 3. Keywords 
                normalized_text = " ".join([str(payload.get(k) or "") for k in ("title", "description", "title_english", "description_english")]).lower()
                good_matches = self._count_keyword_matches(normalized_text, good_keywords)
                bad_matches = self._count_keyword_matches(normalized_text, bad_keywords)

                # Hebrew letters (a word of 2+ letters; Yiddish/Ladino included) in the
                # original title or description count as one good keyword.
                original_text = f"{payload.get('title') or ''} {payload.get('description') or ''}"
                hebrew_in_text = bool(HEBREW_WORD.search(original_text))
                if hebrew_in_text:
                    good_matches += 1

                if good_matches > 0 and bad_matches > 0:
                    payload["status"] = "Not sure"
                    payload["status_details"] = f"{good_matches} good keyword{'s' if good_matches != 1 else ''} and {bad_matches} bad keyoword{'s' if good_matches != 1 else ''} matched"
                elif good_matches > 0:
                    payload["status"] = "Approved"
                    payload["status_details"] = f"{good_matches} good keyword{'s' if good_matches != 1 else ''} matched"
                elif bad_matches > 0:
                    payload["status"] = "Not relevant"
                    payload["status_details"] = ""
                else:
                    payload["status"] = "Not sure"
                    payload["status_details"] = ""

                payload["matched_good_keywords"] = [kw for kw in good_keywords if kw.lower() in normalized_text]
                payload["matched_bad_keywords"] = [kw for kw in bad_keywords if kw.lower() in normalized_text]
                if hebrew_in_text:
                    payload["matched_good_keywords"].append("Hebrew letters")
                return self._note_translation_failure(payload)

        payload["matched_good_keywords"] = []
        payload["matched_bad_keywords"] = []
        return self._note_translation_failure(payload)

    def _count_keyword_matches(self, text: str, keywords: list) -> int:
        text = text.lower()
        return sum(
            1 for kw in keywords
            if kw and re.search(rf"\b{re.escape(kw.lower())}\b", text)
        )

    def _is_government_domain(self, hostname: str | None) -> bool:
        """Return True when a hostname belongs to a government domain."""
        host = (hostname or "").strip().lower().strip(".")
        if not host:
            return False
        return host == "gov" or host.endswith(".gov") or ".gov." in host

    def get_keyword_lists(self) -> tuple[list, list]:
        """Return ordered good and bad keyword lists from the KEYWORDS table."""
        rows = self.get_all_keywords() or []
        good = [r["word"] for r in rows if r.get("good")]
        bad = [r["word"] for r in rows if r.get("good") is False]
        return good, bad

    def search_google_keywords(
        self,
        keywords: list,
        language: str = "en",
        limit: int = 100,
        include_inurl: bool = False,
        homepage_only: bool = False,
        site_domain: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list:
        """Run Google search for keyword list and return normalized, analyzed payloads for review."""
        all_links = []
        self.last_search_errors = []
        for kw in keywords:
            query = f"inurl:{kw}" if include_inurl else kw
            try:
                links = self.google_search(
                    query,
                    num_results=limit,
                    language=language,
                    site_domain=site_domain,
                    date_from=date_from,
                    date_to=date_to,
                )
            except Exception as e:
                # Remember the failure so the user is told about it (bad key, quota, ...)
                self.last_search_errors.append((kw, str(e)))
                links = []
            all_links.extend([(l, kw) for l in links])

        if keywords and len(self.last_search_errors) == len(keywords):
            raise RuntimeError(
                f"Google search failed for every keyword. First error: {self.last_search_errors[0][1]}"
            )

        seen_domain_keys = set()
        root_urls = []
        domain_to_keyword = {}
        domain_to_original_link = {}

        for link, kw in all_links:
            raw = link
            if not raw:
                continue
            if not raw.startswith("http://") and not raw.startswith("https://"):
                raw = "http://" + raw

            parsed = urlparse(raw)
            netloc = (parsed.netloc or "").lower()
            if not netloc:
                continue

            hostname = (parsed.hostname or "").lower()
            if self._is_government_domain(hostname):
                continue

            domain_key = netloc[4:] if netloc.startswith("www.") else netloc
            domain_root = f"{parsed.scheme}://{parsed.netloc}"

            if homepage_only:
                path_clean = parsed.path.strip("/")
                if path_clean or parsed.query or parsed.fragment:
                    continue

            if domain_key in seen_domain_keys:
                continue

            if self.domain_exists(domain_key):
                continue

            seen_domain_keys.add(domain_key)
            root_urls.append(domain_root)
            domain_to_keyword[domain_root] = kw
            domain_to_original_link[domain_root] = raw

        good_keywords, bad_keywords = self.get_keyword_lists()
        processed = []
        # Parents are processed before their subdomains, so a subdomain can inherit the
        # status of a parent found in the same search.
        root_urls.sort(key=lambda u: (urlparse(u).hostname or "").count("."))
        batch_statuses = {}
        # Per-search translation breaker: see _translate_to_english
        self._translation_breaker_active = True
        self._translation_failures = 0
        try:
            for link in root_urls:
                payload = self.analyze_and_extract_url(link)
                payload["link"] = domain_to_original_link.get(link, link)
                if domain_to_keyword.get(link):
                    payload["source"] = f"Google search for {domain_to_keyword[link]}"
                if self._is_unreachable(payload):
                    payload = self._note_translation_failure(self._mark_unreachable(payload))
                else:
                    payload = self.evaluate_payload(payload, good_keywords=good_keywords, bad_keywords=bad_keywords)
                    payload = self.apply_parent_status(payload, good_keywords, bad_keywords, batch_statuses)
                    batch_statuses[self._host_of(payload.get("url") or "")] = payload.get("status")
                processed.append(payload)
        finally:
            self._translation_breaker_active = False

        return processed

    # =========================================================================
    # PARENT DOMAINS AND SUBDOMAINS
    # =========================================================================

    def _host_of(self, url: str) -> str:
        """Lower-case host of a url without a leading 'www.'."""
        host = (urlparse(url if "://" in url else "http://" + url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host

    def _ancestor_hosts(self, host: str) -> list:
        """Parent domains of a host, nearest first: a.b.yivo.org -> [b.yivo.org, yivo.org].

        Single-label names (org) and shared-hosting platforms are never parents.
        """
        labels = host.split(".")
        ancestors = [".".join(labels[i:]) for i in range(1, len(labels) - 1)]
        return [a for a in ancestors if a not in SHARED_HOSTING_DOMAINS]

    def find_parent_status(self, host: str, batch_statuses: dict | None = None):
        """Status of the nearest saved parent domain: (parent_host, status), or None.

        Rows of unreachable sites are ignored: their status describes a dead site, not a decision.
        """
        if not host or _IP_ADDRESS.match(host):
            return None
        ancestors = self._ancestor_hosts(host)
        if not ancestors:
            return None

        urls = []
        for a in ancestors:
            urls += [a, f"http://{a}", f"https://{a}", f"http://www.{a}", f"https://www.{a}", f"www.{a}"]
        saved = {}
        try:
            rows = (
                self.supabase.table("DOMAINS")
                .select("url,status,status_details")
                .in_("url", urls)
                .execute()
                .data
                or []
            )
        except Exception:
            rows = []
        for row in rows:
            if "unreachable" in (row.get("status_details") or "").lower():
                continue
            saved.setdefault(self._host_of(row.get("url") or ""), row.get("status"))

        for a in ancestors:
            status = (batch_statuses or {}).get(a) or saved.get(a)
            if status:
                return a, status
        return None

    def _keyword_counts(self, payload: dict, good_keywords: list, bad_keywords: list) -> tuple:
        """(good, bad) keyword match counts of a payload; Hebrew letters count as one good keyword."""
        text = " ".join(
            str(payload.get(k) or "") for k in ("title", "description", "title_english", "description_english")
        ).lower()
        good = self._count_keyword_matches(text, good_keywords)
        bad = self._count_keyword_matches(text, bad_keywords)
        if HEBREW_WORD.search(f"{payload.get('title') or ''} {payload.get('description') or ''}"):
            good += 1
        return good, bad

    def _parent_decision(self, auto_status: str, good_count: int, bad_count: int, parent_status: str) -> str:
        """Status a subdomain gets from its parent: the parent's status, or 'Not sure' on a conflict."""
        if parent_status == "Approved":
            if auto_status == "Approved":
                return "Approved"
            conflict = auto_status == "Not relevant" or bad_count > 0
        else:  # parent is Not relevant
            if auto_status == "Not relevant":
                return "Not relevant"
            conflict = auto_status == "Approved" or good_count > 0
        return "Not sure" if conflict else parent_status

    def apply_parent_status(self, payload: dict, good_keywords: list, bad_keywords: list, batch_statuses: dict | None = None) -> dict:
        """Apply the shared-hosting rule and the parent-domain rule to an evaluated payload.

        - The main domain of a shared-hosting platform is Not relevant.
        - A subdomain whose nearest saved parent is Approved / Not relevant gets that status,
          unless its own keywords disagree: then it is 'Not sure'.
        - A parent that is 'Not sure' (or missing) passes nothing down.
        """
        host = self._host_of(payload.get("url") or "")
        if host in SHARED_HOSTING_DOMAINS:
            payload["status"] = "Not relevant"
            payload["status_details"] = "Shared hosting platform"
            return payload

        found = self.find_parent_status(host, batch_statuses)
        if not found or found[1] not in ("Approved", "Not relevant"):
            return payload
        parent_host, parent_status = found

        good_count, bad_count = self._keyword_counts(payload, good_keywords, bad_keywords)
        new_status = self._parent_decision(payload.get("status"), good_count, bad_count, parent_status)
        original_details = payload.get("status_details") or ""
        if new_status == parent_status:
            prefix = f"Inherited from {parent_host} ({parent_status})"
        else:
            prefix = f"Conflicts with parent {parent_host} ({parent_status})"
        payload["status"] = new_status
        payload["status_details"] = f"{prefix}; {original_details}" if original_details else prefix
        return payload

    def cascade_status_to_subdomains(self, parent_url: str, parent_status: str, user: str | None = None) -> list:
        """After a person sets a parent to Approved / Not relevant, pass that status down.

        Only existing subdomains whose status is currently 'Not sure' change, and only when their
        own keywords do not conflict. Subdomains with a nearer saved parent, and anything under a
        shared-hosting platform's main domain, are left alone. Returns the urls that changed.
        """
        if parent_status not in ("Approved", "Not relevant"):
            return []
        host = self._host_of(parent_url)
        if not host or _IP_ADDRESS.match(host) or host in SHARED_HOSTING_DOMAINS:
            return []

        rows = (
            self.supabase.table("DOMAINS")
            .select("*")
            .ilike("url", f"%.{host}%")
            .execute()
            .data
            or []
        )
        descendants = {}
        for row in rows:
            h = self._host_of(row.get("url") or "")
            if h.endswith("." + host):
                descendants[h] = row
        if not descendants:
            return []

        good_keywords, bad_keywords = self.get_keyword_lists()
        changed = []
        for h, row in descendants.items():
            if row.get("status") != "Not sure":
                continue
            # A nearer saved parent (between this subdomain and the changed parent) decides instead
            nearer = [
                a for a in self._ancestor_hosts(h)
                if a != host and a in descendants
                and "unreachable" not in (descendants[a].get("status_details") or "").lower()
            ]
            if nearer:
                continue

            payload = {
                k: row.get(k)
                for k in ("url", "title", "description", "title_english", "description_english", "languages")
            }
            payload = self.evaluate_payload(payload, good_keywords=good_keywords, bad_keywords=bad_keywords)
            good_count, bad_count = self._keyword_counts(payload, good_keywords, bad_keywords)
            if self._parent_decision(payload["status"], good_count, bad_count, parent_status) != parent_status:
                continue  # conflicting keywords: stays 'Not sure'

            updated = dict(row)
            updated["status"] = parent_status
            updated["status_details"] = f"Inherited from {host} ({parent_status})"
            if user:
                updated["user"] = user
            self.insert_domain(updated)
            changed.append(row.get("url"))
        return changed

    def _is_unreachable(self, payload: dict) -> bool:
        """No response at all, 404, 410 or a server error (5xx). 403/401/429 count as reachable."""
        code = payload.get("response_code")
        return code is None or code in (404, 410) or code >= 500

    def _mark_unreachable(self, payload: dict) -> dict:
        """Label an unreachable site: Not relevant, except .il sites which stay Approved."""
        code = payload.get("response_code")
        reason = "no response" if code is None else f"HTTP {code}"
        hostname = (urlparse(payload.get("url") or "").hostname or "").lower()
        if hostname.endswith(".il"):
            payload["status"] = "Approved"
            payload["status_details"] = f".il suffix; unreachable ({reason})"
        else:
            payload["status"] = "Not relevant"
            payload["status_details"] = f"Unreachable ({reason})"
        if isinstance(payload.get("languages"), dict):
            # A failed fetch carries a fake {"error": ...} language; drop it
            payload["languages"] = None
        payload["matched_good_keywords"] = []
        payload["matched_bad_keywords"] = []
        payload["unreachable"] = True
        return payload

    def domain_exists(self, domain_url: str) -> bool:
        """Check whether a domain already exists in `DOMAINS`."""
        try:
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

    def _sanitize_domain_payload(self, payload: dict) -> dict:
        """Keep only allowed DOMAINS fields and drop internal evaluation keys."""
        allowed = {
            "url",
            "title",
            "description",
            "title_english",
            "description_english",
            "languages",
            "status",
            "status_details",
            "response_code",
            "source",
            "user",
            "created_at",
            "updated_at",
            "id",
        }
        sanitized = {k: v for k, v in payload.items() if k in allowed}
        return sanitized

    def insert_domain(self, payload: dict):
        """Upserts processed metadata into the DOMAINS table using strictly Jerusalem time."""
        now_iso = self._now_jerusalem_iso()
        # Preserve original `created_at` when updating an existing record.
        existing_row = None
        try:
            if payload.get("url"):
                resp = (
                    self.supabase.table("DOMAINS")
                    .select("*")
                    .eq("url", payload.get("url"))
                    .limit(1)
                    .execute()
                )
                if resp and getattr(resp, "data", None):
                    if resp.data:
                        existing_row = resp.data[0]
        except Exception:
            existing_row = None

        if existing_row and existing_row.get("created_at"):
            payload["created_at"] = existing_row.get("created_at")
        else:
            # New record: ensure created_at is set
            if not payload.get("created_at"):
                payload["created_at"] = now_iso

        # Always update the updated_at timestamp
        payload["updated_at"] = now_iso

        # If a `user` value is provided in the payload, use it (this will overwrite
        # any previous user and ensure only the last user is saved). If not provided,
        # preserve existing user when updating.
        if existing_row:
            if not payload.get("user") and existing_row.get("user"):
                payload["user"] = existing_row.get("user")

        sanitized = self._sanitize_domain_payload(payload)
        return (
            self.supabase.table("DOMAINS")
            .upsert(sanitized, on_conflict="url")
            .execute()
        )

    def insert_domains_batch(self, payloads: list) -> int:
        """Save many NEW domain rows in a single request (used by the search auto-save).

        Rows whose url already exists are left untouched (never overwritten).
        Raises on failure so the caller can fall back to saving row by row.
        """
        now_iso = self._now_jerusalem_iso()
        columns = [
            "url", "title", "description", "title_english", "description_english",
            "languages", "status", "status_details", "response_code", "source", "user",
        ]
        rows = {}
        for payload in payloads:
            if not payload.get("url"):
                continue
            row = {col: payload.get(col) for col in columns}
            row["created_at"] = now_iso
            row["updated_at"] = now_iso
            rows[row["url"]] = row
        if rows:
            (
                self.supabase.table("DOMAINS")
                .upsert(list(rows.values()), on_conflict="url", ignore_duplicates=True)
                .execute()
            )
        return len(rows)

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
        # Commas, parentheses and line breaks separate the search into several
        # terms; each term is searched in turn and the results are combined.
        rows_by_id = {}
        for term in self._split_search_terms(query):
            match_str = f"%{term}%"
            response = (
                self.supabase.table("DOMAINS")
                .select("*")
                .or_(
                    f"url.ilike.{match_str},title.ilike.{match_str},description.ilike.{match_str},title_english.ilike.{match_str},description_english.ilike.{match_str}"
                )
                .order("id", desc=True)
                .execute()
            )
            for row in response.data or []:
                rows_by_id.setdefault(row["id"], row)
        return sorted(rows_by_id.values(), key=lambda r: r["id"], reverse=True)

    def _split_search_terms(self, query: str) -> list:
        """Split search text on commas, parentheses and line breaks."""
        return [t.strip() for t in re.split(r"[,()\n]+", query or "") if t.strip()]

    def advanced_search_domains(self, filters: dict) -> list:
        """Executes a compound multi-column advanced filter search query."""
        # Each text term (split on commas, parentheses, line breaks) is searched
        # separately with the other filters applied, and the results are combined.
        terms = self._split_search_terms(filters.get("text_query")) or [None]
        rows_by_id = {}
        for term in terms:
            query_builder = self.supabase.table("DOMAINS").select("*")

            if term:
                match_str = f"%{term}%"
                query_builder = query_builder.or_(
                    f"url.ilike.{match_str},title.ilike.{match_str},description.ilike.{match_str},title_english.ilike.{match_str},description_english.ilike.{match_str}"
                )

            if filters.get("status"):
                query_builder = query_builder.eq("status", filters["status"])

            if filters.get("response_code"):
                query_builder = query_builder.eq("response_code", filters["response_code"])

            response = query_builder.order("id", desc=True).execute()
            for row in response.data or []:
                rows_by_id.setdefault(row["id"], row)
        data = sorted(rows_by_id.values(), key=lambda r: r["id"], reverse=True)

        if filters.get("language") and data:
            target_lang = filters["language"].strip().lower()
            # Accept both a name ("Hebrew") and a code ("he"): a code is also
            # matched against its language name, since names are what is stored.
            wanted_langs = {target_lang}
            if re.match(r"^[a-z]{2,3}(-[a-z0-9]{2,4})?$", target_lang):
                wanted_langs.add(self._lang_code_to_name(target_lang).lower())
            data = [
                row for row in data
                if (
                    (isinstance(row.get("languages"), dict) and any(str(lang).lower() in wanted_langs for lang in row["languages"]))
                    or (isinstance(row.get("languages"), list) and any(str(lang).lower() in wanted_langs for lang in row.get("languages", [])))
                )
            ]

        return data

    # =========================================================================
    # KEYWORDS TABLE MANAGEMENT
    # =========================================================================

    def analyze_keywords_before_saving(self, words: list) -> list:
        """Splits text input and runs language detection on each word token."""
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
        """Saves a prepared and finalized payload batch using strictly Jerusalem time."""
        if not payloads:
            return {"success": False, "message": "No data provided."}

        try:
            now_iso = self._now_jerusalem_iso()
            for p in payloads:
                if not p.get("created_at"):
                    p["created_at"] = now_iso

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
