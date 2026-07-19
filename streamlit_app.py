import streamlit as st
import pandas as pd
from backend import ArchiveBackend
from urllib.parse import urlparse
import time
import re
import datetime

# --- PAGE SETUP ---
st.set_page_config(
    page_title="Israeli Internet Archive Database Manager",
    page_icon="🌐",
    layout="wide",
)


@st.cache_resource
def get_backend():
    return ArchiveBackend()

# Ensure cached instance is cleared so changes to ArchiveBackend are picked up
try:
    get_backend.clear()
except Exception:
    pass

backend = get_backend()

ALLOWED_USER_NAMES = ["hana", "roi", "yehonatan"]


def get_session_user() -> str:
    if "domain_user" not in st.session_state:
        st.session_state["domain_user"] = ""
    return st.session_state["domain_user"]


def normalize_user_name(raw_value: str | None) -> str | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    for allowed_name in ALLOWED_USER_NAMES:
        if value.lower() == allowed_name.lower():
            return allowed_name
    return None


def resolve_status_details(
    status: str,
    provided_details: str | None = None,
    fallback_details: str | None = None,
    status_changed: bool = False,
) -> str:
    if not status_changed:
        return fallback_details or provided_details or ""
    if status in {"Not relevant", "Not sure"}:
        return "Determined by user"
    if status == "Approved":
        return provided_details or fallback_details or "Determined by user"
    return provided_details or fallback_details or ""


# --- AUTHENTICATION SHIELD ---
def check_password():
    """Returns True if the user entered the correct password and a valid user name."""
    def password_entered():
        entered_user = normalize_user_name(st.session_state.get("password_user_input", ""))
        if st.session_state.get("password") == st.secrets["APP_PASSWORD"] and entered_user:
            st.session_state["password_correct"] = True
            st.session_state["domain_user"] = entered_user
            st.session_state.pop("password", None)
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        with st.form("auth_form"):
            st.text_input(
                "User",
                value=get_session_user(),
                key="password_user_input",
            )
            st.text_input("Password", type="password", key="password")
            submitted = st.form_submit_button("Continue")

        if submitted:
            password_entered()
        return False
    elif not st.session_state["password_correct"]:
        with st.form("auth_form"):
            st.text_input("Please enter your access password", type="password", key="password")
            st.text_input(
                "User",
                value=get_session_user(),
                help="Enter one name only: Hana, Roi, or Yehonatan.",
                key="password_user_input",
            )
            submitted = st.form_submit_button("Continue")

        if submitted:
            password_entered()
        st.error("😕 Password incorrect or invalid user")
        return False
    else:
        return True


if check_password():
    st.title("🇮🇱 Israeli Internet Archive Management Portal")

    # =========================================================================
    # RESTRUCTURED SIDEBAR MENU NAVIGATION
    # =========================================================================
    st.sidebar.title("🗂️ Navigation Control")
    
    option = st.sidebar.radio(
        "Choose App Tool Engine:",
        [
            "🌐 Add URLs using Search",
            "🌐 Add URLs Manually", 
            "🌐 Domains Database Explorer", 
            "🏷️ Add Keywords",
            "🏷️ Keywords Database Explorer"
        ]
    )

    # --- VIEW 1: MANUAL URL INGESTION ---
    if option == "🌐 Add URLs Manually":
        st.header("📝 Manual URL Extraction and Ingestion")

        good_kw_list, bad_kw_list = backend.get_keyword_lists()

        with st.form("url_fetch_form"):
            input_url = st.text_area(
                "Target URL(s)",
                placeholder="example.co.il\nexample.org\nhttps://sub.example.com/path",
                help="Enter one URL per line or comma-separated. URLs containing paths will be normalized to their root domain when needed.",
            )
            fetch_btn = st.form_submit_button("Analyze Domain")

        if fetch_btn:
            entered_user = normalize_user_name(get_session_user())
            if not entered_user:
                st.error("User must be one of: Hana, Roi, Yehonatan.")
            else:
                st.session_state["domain_user"] = entered_user
                urls = [u.strip() for u in re.split(r"[\n,]+", input_url) if u.strip()]
                if not urls:
                    st.warning("Please specify at least one URL first.")
                else:
                    invalid_urls = [u for u in urls if not backend.is_valid_url_format(u)]
                    if invalid_urls:
                        st.error(
                            "❌ Invalid URL structure found. Please remove or fix the following entries: "
                            + ", ".join(invalid_urls)
                        )

                    valid_urls = [u for u in urls if backend.is_valid_url_format(u)]
                    if not valid_urls:
                        st.session_state["show_editor"] = False
                    elif len(valid_urls) == 1:
                        input_url = valid_urls[0]
                        manual_info = backend.prepare_manual_url(input_url)

                        if manual_info["has_extra_path"]:
                            st.session_state["manual_original_url"] = manual_info["normalized_url"]
                            st.session_state["manual_trimmed_url"] = manual_info["trimmed_url"]
                            st.session_state["manual_pending_domain"] = manual_info["domain_key"]
                            st.session_state["manual_url_needs_choice"] = True
                            st.session_state["manual_domain_exists"] = False
                            st.session_state["show_editor"] = False
                        else:
                            st.session_state["manual_url_needs_choice"] = False
                            st.session_state["manual_pending_url"] = manual_info["normalized_url"]
                            st.session_state["manual_pending_domain"] = manual_info["domain_key"]
                            if manual_info["domain_exists"]:
                                st.session_state["manual_domain_exists"] = True
                                st.session_state["show_editor"] = False
                            else:
                                with st.spinner("Analyzing and parsing remote server contents..."):
                                    scraped_payload = backend.analyze_and_extract_url(manual_info["normalized_url"])
                                    scraped_payload = backend.evaluate_payload(
                                        scraped_payload,
                                        good_keywords=good_kw_list,
                                        bad_keywords=bad_kw_list,
                                    )
                                scraped_payload["status"] = "Approved"
                                scraped_payload["link"] = manual_info["normalized_url"]
                                scraped_payload["user"] = entered_user
                                st.session_state["manual_payloads"] = [scraped_payload]
                                st.session_state["show_editor"] = True
                                st.session_state["manual_domain_exists"] = False
                    else:
                        batch_payloads = []
                        skipped_existing = []
                        with st.spinner("Analyzing and parsing remote server contents..."):
                            for url in valid_urls:
                                manual_info = backend.prepare_manual_url(url)
                                if manual_info["domain_exists"]:
                                    skipped_existing.append(manual_info["normalized_url"])
                                    continue

                                analysis_url = manual_info["trimmed_url"] if manual_info["has_extra_path"] else manual_info["normalized_url"]
                                scraped_payload = backend.analyze_and_extract_url(analysis_url)
                                scraped_payload = backend.evaluate_payload(
                                    scraped_payload,
                                    good_keywords=good_kw_list,
                                    bad_keywords=bad_kw_list,
                                )
                                scraped_payload["status"] = "Approved"
                                scraped_payload["link"] = analysis_url
                                scraped_payload["original_input"] = url
                                scraped_payload["user"] = entered_user
                                batch_payloads.append(scraped_payload)

                        if skipped_existing:
                            st.info(
                                "The following domains were already present and were skipped: "
                                + ", ".join(skipped_existing)
                            )

                        if batch_payloads:
                            st.session_state["manual_payloads"] = batch_payloads
                            st.session_state["show_editor"] = True
                        else:
                            st.session_state["show_editor"] = False

        if st.session_state.get("manual_url_needs_choice"):
            st.warning(
                "The URL you entered includes a path/query/fragment and is not a bare domain or subdomain."
            )
            st.info("Choose whether to analyze the full URL or trim it to the domain root before continuing.")
            with st.form("manual_url_choice_form"):
                url_choice = st.radio(
                    "Select the preferred URL to analyze:",
                    ["Continue with full URL", "Trim to domain root"],
                    index=1,
                )
                choice_btn = st.form_submit_button("Continue")

            if choice_btn:
                analysis_url = (
                    st.session_state["manual_original_url"]
                    if url_choice == "Continue with full URL"
                    else st.session_state["manual_trimmed_url"]
                )
                st.session_state["manual_pending_url"] = analysis_url
                st.session_state["manual_url_needs_choice"] = False

                if backend.domain_exists(st.session_state["manual_pending_domain"]):
                    st.session_state["manual_domain_exists"] = True
                    st.session_state["show_editor"] = False
                else:
                    with st.spinner("Analyzing and parsing remote server contents..."):
                        scraped_payload = backend.analyze_and_extract_url(analysis_url)
                        scraped_payload = backend.evaluate_payload(
                            scraped_payload,
                            good_keywords=good_kw_list,
                            bad_keywords=bad_kw_list,
                        )
                        scraped_payload["link"] = analysis_url
                        scraped_payload["user"] = st.session_state.get("domain_user", "")
                        st.session_state["current_payload"] = scraped_payload
                        st.session_state["show_editor"] = True
                        st.session_state["manual_domain_exists"] = False

        if st.session_state.get("manual_domain_exists"):
            st.warning(f"The domain `{st.session_state.get('manual_pending_domain')}` already exists in the database.")
            with st.form("existing_domain_choice_form"):
                existing_choice = st.radio(
                    "Do you want to reanalyze it or skip?",
                    ["Reanalyze and update", "Skip"],
                    index=0,
                )
                existing_choice_btn = st.form_submit_button("Continue")

            if existing_choice_btn:
                if existing_choice == "Reanalyze and update":
                    with st.spinner("Reanalyzing existing domain..."):
                        scraped_payload = backend.analyze_and_extract_url(st.session_state.get("manual_pending_url"))
                        scraped_payload = backend.evaluate_payload(
                            scraped_payload,
                            good_keywords=good_kw_list,
                            bad_keywords=bad_kw_list,
                        )
                        scraped_payload["link"] = st.session_state.get("manual_pending_url")
                        scraped_payload["user"] = st.session_state.get("domain_user", "")
                        st.session_state["current_payload"] = scraped_payload
                        st.session_state["show_editor"] = True
                else:
                    st.info("Skipped analysis for the existing domain.")
                    st.session_state["show_editor"] = False

                st.session_state["manual_domain_exists"] = False

        if st.session_state.get("show_editor"):
            payloads = st.session_state.get("manual_payloads", [])
            if not payloads and st.session_state.get("current_payload"):
                payloads = [st.session_state.get("current_payload")]

            if payloads:
                st.write("---")
                st.subheader("🔍 Review and Edit Extracted Metadata")
                if len(payloads) > 1:
                    st.info("Review each URL result below before committing the batch to the archive.")

                with st.form("metadata_edit_form"):
                    st.subheader("Batch Settings")
                    global_source = st.text_input(
                        "Source (applies to all URLs)",
                        value="manually",
                        help="Enter the source for all URLs in this batch",
                    )

                    for idx, payload in enumerate(payloads):
                        chosen_url = st.session_state.get("manual_pending_url", payload.get("url"))
                        url_label = payload.get("link", chosen_url)
                        with st.expander(f"Item {idx + 1}: {url_label}", expanded=True):
                            st.markdown(f"**URL:** {url_label}")
                            col1, col2 = st.columns(2)

                            with col1:
                                st.metric(label="Server Response Code", value=payload["response_code"] or "N/A")
                                status_options = ["Approved", "Not sure", "Not relevant"]
                                edited_status = st.selectbox(
                                    "Status Evaluation",
                                    options=status_options,
                                    index=0,
                                    key=f"status_{idx}",
                                )
                                edited_status_details = st.text_input(
                                    "Status Details",
                                    value=payload.get("status_details", "") or "",
                                    key=f"details_{idx}",
                                )
                                edited_title = st.text_input(
                                    "Extracted Title",
                                    value=payload.get("title") or "",
                                    key=f"title_{idx}",
                                )
                                edited_description = st.text_area(
                                    "Extracted Description",
                                    value=payload.get("description") or "",
                                    key=f"description_{idx}",
                                )

                            with col2:
                                languages_data = payload.get("languages")
                                detected_langs_list = []
                                if isinstance(languages_data, list):
                                    detected_langs_list = languages_data
                                elif isinstance(languages_data, dict):
                                    detected_langs_list = list(languages_data.keys())

                                edited_langs_str = st.text_input(
                                    "Languages List (comma-separated)",
                                    value=", ".join(detected_langs_list),
                                    key=f"langs_{idx}",
                                )
                                st.text_input(
                                    "English Title Translation",
                                    value=payload.get("title_english") or "",
                                    disabled=True,
                                    key=f"title_en_{idx}",
                                )
                                st.text_area(
                                    "English Description Translation",
                                    value=payload.get("description_english") or "",
                                    disabled=True,
                                    key=f"description_en_{idx}",
                                )

                            matched_good = payload.get("matched_good_keywords") or []
                            matched_bad = payload.get("matched_bad_keywords") or []
                            if matched_good or matched_bad:
                                st.markdown("**Matched Keywords**")
                                if matched_good:
                                    st.success(f"Good keywords matched: {', '.join(matched_good)}")
                                else:
                                    st.info("No good keywords matched.")
                                if matched_bad:
                                    st.warning(f"Bad keywords matched: {', '.join(matched_bad)}")
                                else:
                                    st.info("No bad keywords matched.")

                    submit_to_db = st.form_submit_button("Save and Commit to Archive")

                    if submit_to_db:
                        committed = []
                        failed = []
                        session_user = normalize_user_name(get_session_user())
                        if not session_user:
                            st.error("User must be one of: Hana, Roi, Yehonatan.")
                        else:
                            st.session_state["domain_user"] = session_user
                            for idx, payload in enumerate(payloads):
                                edited_status = st.session_state.get(f"status_{idx}", "Approved")
                                edited_status_details = st.session_state.get(f"details_{idx}", payload.get("status_details", ""))
                                edited_title = st.session_state.get(f"title_{idx}", payload.get("title") or "")
                                edited_description = st.session_state.get(f"description_{idx}", payload.get("description") or "")
                                edited_langs_str = st.session_state.get(f"langs_{idx}", "")
                                cleaned_langs = [l.strip() for l in edited_langs_str.split(",") if l.strip()]

                                previous_status = payload.get("status")
                                payload["status"] = edited_status
                                payload["status_details"] = resolve_status_details(
                                    edited_status,
                                    edited_status_details,
                                    payload.get("status_details"),
                                    status_changed=(edited_status != previous_status),
                                )
                                payload["title"] = edited_title or None
                                payload["description"] = edited_description or None
                                payload["languages"] = cleaned_langs if cleaned_langs else None
                                payload["source"] = global_source
                                payload["user"] = session_user
                                payload.pop("link", None)
                                payload.pop("original_input", None)

                                try:
                                    backend.insert_domain(payload)
                                    committed.append(payload["url"])
                                except Exception as db_err:
                                    failed.append((payload.get("url"), str(db_err)))

                            if committed:
                                st.success(f"Successfully registered {len(committed)} URL(s) to the Database!")
                                st.session_state["show_editor"] = False
                                st.session_state.pop("manual_payloads", None)
                            if failed:
                                for failed_url, err in failed:
                                    st.error(f"Failed to save {failed_url}: {err}")

    # --- VIEW 2: CENTRAL URL EXPLORER ENGINE ---
    elif option == "🌐 Domains Database Explorer":
        st.header("📊 Archive Database Explorer Dashboard")
        search_tab, view_all_tab = st.tabs(["🔍 Search Engine Panel", "📋 View All Records Grid"])

        with view_all_tab:
            st.subheader("All System Database Records")
            with st.spinner("Loading row matrices..."):
                try:
                    all_rows = backend.get_all_domains()
                    if all_rows:
                        df = pd.DataFrame(all_rows)
                        if "languages" in df.columns:
                            def normalize_langs(value):
                                if isinstance(value, list):
                                    return ", ".join(str(v) for v in value)
                                if isinstance(value, dict):
                                    return ", ".join(str(k) for k in value.keys())
                                if value is None:
                                    return ""
                                return str(value)

                            df["languages"] = df["languages"].apply(normalize_langs)

                        ordered_cols = [
                            "id",
                            "url",
                            "title",
                            "description",
                            "title_english",
                            "description_english",
                            "languages",
                            "status",
                            "status_details",
                            "source",
                            "response_code",
                            "user",
                            "created_at",
                            "updated_at"
                        ]
                        existing_cols = [c for c in ordered_cols if c in df.columns]
                        df = df[existing_cols]
                        st.dataframe(df, use_container_width=True, hide_index=True)
                    else:
                        st.info("The database is currently empty.")
                except Exception as ex:
                    st.error(f"Failed to compile records view: {ex}")

        with search_tab:
            search_mode = st.radio("Select Search Paradigm:", ["Regular Text Search", "Advanced Filter Search"], horizontal=True)

            if search_mode == "Regular Text Search":
                simple_query = st.text_input("Enter simple matching text phrase keyword:", placeholder="e.g., news, security, חדשות")
                results = backend.search_domains(simple_query) if simple_query else None
            else:
                st.markdown("#### Advanced Filtering Combinations")
                with st.expander("Configure Complex Query Criteria Parameters", expanded=True):
                    c1, c2, c3, c4 = st.columns(4)
                    with c1:
                        adv_text = st.text_input("Contains Text String", placeholder="Any keyword match...")
                    with c2:
                        adv_status = st.selectbox("Status Option Matches", ["All Options", "Approved", "Not sure", "Not relevant"])
                    with c3:
                        adv_lang = st.text_input("Target Language Shortcode", placeholder="e.g., he, en, ar")
                    with c4:
                        adv_code = st.text_input("HTTP Response Code Status", placeholder="e.g., 200, 404")

                    run_advanced = st.button("Execute Compound Search Query", type="primary")
                
                results = None
                if run_advanced:
                    with st.spinner("Processing advanced parameter index..."):
                        filters = {}
                        if adv_text: filters["text_query"] = adv_text
                        if adv_status != "All Options": filters["status"] = adv_status
                        if adv_lang: filters["language"] = adv_lang
                        if adv_code: 
                            try: filters["response_code"] = int(adv_code)
                            except ValueError: st.warning("Response code filter skipped.")

                        results = backend.advanced_search_domains(filters)

            if results is not None:
                st.write(f"Query returned **{len(results)}** matching index records.")
                for idx, row in enumerate(results):
                    header_title = row.get("title") or row.get("title_english") or "No Title Available"
                    edit_key = f"edit_record_{idx}"
                    with st.expander(f"🌐 {row['url']} — {header_title}"):
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            st.write(f"**Original Title:** {row.get('title')}")
                            st.write(f"**Original Description:** {row.get('description')}")
                            st.write(f"**Response Code:** `{row.get('response_code')}` | **Status:** `{row.get('status')}`")
                        with col2:
                            st.button(
                                "Edit",
                                key=f"{edit_key}_btn",
                                on_click=lambda k=edit_key: st.session_state.__setitem__(k, True),
                            )
                        col3, col4 = st.columns(2)
                        with col3:
                            st.write(f"**English Title:** {row.get('title_english')}")
                            st.write(f"**English Description:** {row.get('description_english')}")
                        with col4:
                            st.write(f"**Languages Map:** `{row.get('languages')}`")
                        st.caption(f"Source: `{row.get('source')}` | Updated: {row.get('updated_at')}")

                        if st.session_state.get(edit_key, False):
                            with st.form(f"edit_row_form_{idx}"):
                                st.markdown("### Edit this record")
                                edited_status = st.selectbox(
                                    "Status:",
                                    options=["Approved", "Not sure", "Not relevant"],
                                    index=["Approved", "Not sure", "Not relevant"].index(row.get("status") if row.get("status") in ["Approved", "Not sure", "Not relevant"] else "Not sure"),
                                )
                                edited_details = st.text_input("Status Details:", value=row.get("status_details") or "")
                                edited_title = st.text_input("Original Title:", value=row.get("title") or "")
                                edited_description = st.text_area("Original Description:", value=row.get("description") or "")
                                edited_title_en = st.text_input("English Title:", value=row.get("title_english") or "")
                                edited_description_en = st.text_area("English Description:", value=row.get("description_english") or "")
                                langs_value = row.get("languages")
                                if isinstance(langs_value, dict):
                                    langs_csv = ", ".join(langs_value.keys())
                                elif isinstance(langs_value, list):
                                    langs_csv = ", ".join(str(l) for l in langs_value)
                                else:
                                    langs_csv = str(langs_value)
                                edited_langs = st.text_input("Languages (comma-separated):", value=langs_csv)

                                save_btn = st.form_submit_button("Save changes")
                                cancel_btn = st.form_submit_button(
                                    "Cancel",
                                    on_click=lambda k=edit_key: st.session_state.__setitem__(k, False),
                                )

                                if save_btn:
                                    updated_row = row.copy()
                                    previous_status = row.get("status")
                                    updated_row["status"] = edited_status
                                    updated_row["status_details"] = resolve_status_details(
                                        edited_status,
                                        edited_details,
                                        updated_row.get("status_details"),
                                        status_changed=(edited_status != previous_status),
                                    )
                                    updated_row["title"] = edited_title or None
                                    updated_row["description"] = edited_description or None
                                    updated_row["title_english"] = edited_title_en or None
                                    updated_row["description_english"] = edited_description_en or None
                                    cleaned_langs = [l.strip() for l in edited_langs.split(",") if l.strip()]
                                    if cleaned_langs:
                                        updated_row["languages"] = {lang: 1.0 for lang in cleaned_langs}
                                    # Ensure current session user is recorded as the last editor
                                    session_user = normalize_user_name(get_session_user())
                                    if not session_user:
                                        st.error("User must be one of: Hana, Roi, Yehonatan.")
                                    else:
                                        updated_row["user"] = session_user
                                        # Do not touch created_at here; backend will preserve it on upsert
                                        try:
                                            backend.insert_domain(updated_row)
                                            st.success(f"Saved updates for {row.get('url')}")
                                            st.session_state[edit_key] = False
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"Failed to save record: {e}")
                                elif cancel_btn:
                                    st.session_state[edit_key] = False
                                    st.rerun()

    # --- VIEW 3: GOOGLE KEYWORD SEARCH ---
    elif option == "🌐 Add URLs using Search":
        st.header("🔍 Google Keyword Search & Ingest Tool")
        st.write("This tool searches Google for keywords and offers an approval workflow for results.")

        language_options = {
            "English (en)": "en", "Hebrew (he)": "he", "Arabic (ar)": "ar", "French (fr)": "fr",
            "German (de)": "de", "Italian (it)": "it", "Russian (ru)": "ru", "Yiddish (yi)": "yi",
            "Dutch (nl)": "nl", "Romanian (ro)": "ro", "Hungarian (hu)": "hu", "Spanish - Latin America (es-419)": "es-419",
            "Spanish - Spain (es-ES)": "es-ES", "Portuguese - Brazil (pt-BR)": "pt-BR", "Portuguese - Portugal (pt-PT)": "pt-PT",
            "Turkish": "tr", "Polish (pl)": "pl",
        }

        def clear_processed_review_state():
            """Remove old review widgets/results so a new search starts cleanly."""
            for key in list(st.session_state.keys()):
                if (
                    key.startswith("status_")
                    or key.startswith("details_")
                    or key.startswith("langs_")
                ):
                    del st.session_state[key]
            st.session_state.pop("ordered_processed", None)

        with st.form("keywords_search_form"):
            keywords_query = st.text_area(
                "Keywords List:",
                help="Enter the keywords you want to search for. Use commas to separate multiple keywords.",
            )

            col1, col2 = st.columns(2)
            with col1:
                selected_language = st.selectbox("Language:", options=list(language_options.keys()))
                language = language_options[selected_language]
            with col2:
                limit = st.selectbox("Max Results:", options=[100, 50, 10], index=0)

            st.divider()

            col3, col4 = st.columns(2)
            with col3:
                include_inurl = st.checkbox("Include 'inurl' in the search", value=False)
                homepage_only = st.checkbox("Include only homepage results", value=False)
            with col4:
                site_domain = st.text_input("Search within a domain", placeholder="chabad.org")

            st.divider()

            date_restrict = st.checkbox(
                    "Restrict results by date range",
                    value=False,
                    help="Date filters are visible always, but applied only when this checkbox is checked.",
                )
            
            date_col1, date_col2 = st.columns(2)
            with date_col1:
                date_from = st.date_input(
                    "From date",
                    value=datetime.date(1995, 1, 1),
                    min_value=datetime.date(1995, 1, 1),
                    max_value=datetime.date.today(),
                    key="search_from_date",
                )
            with date_col2:
                date_to = st.date_input(
                    "To date",
                    value=datetime.date.today(),
                    min_value=datetime.date(1995, 1, 1),
                    max_value=datetime.date.today(),
                    key="search_to_date",
                )

            run_search = st.form_submit_button("Run Keyword Search")

        if not date_restrict:
            date_from = None
            date_to = None

        if run_search:
            clear_processed_review_state()

            entered_user = normalize_user_name(get_session_user())
            if not entered_user:
                st.error("User must be one of: Hana, Roi, Yehonatan.")
            else:
                st.session_state["domain_user"] = entered_user
                if not keywords_query.strip():
                    st.warning("Please provide keywords to search for.")
                else:
                    keywords = [k.strip() for k in keywords_query.split(",") if k.strip()]
                    try:
                        processed = backend.search_google_keywords(
                            keywords,
                            language=language,
                            limit=limit,
                            include_inurl=include_inurl,
                            homepage_only=homepage_only,
                            site_domain=site_domain.strip() if site_domain else None,
                            date_from=date_from.isoformat() if date_from else None,
                            date_to=date_to.isoformat() if date_to else None,
                        )
                    except Exception as e:
                        st.error(f"Search failed: {e}")
                        processed = []

                    st.success(f"Collected {len(processed)} unique domain roots from search results.")
                    st.session_state["ordered_processed"] = (
                        [p for p in processed if p.get("status") != "Approved"]
                        + [p for p in processed if p.get("status") == "Approved"]
                    )

        ordered_processed = st.session_state.get("ordered_processed", [])

        if ordered_processed:
            st.write("---")
            status_options = ["Approved", "Not sure", "Not relevant"]

            approved_count = len([p for p in ordered_processed if p.get("status") == "Approved"])
            unknown_count = len([p for p in ordered_processed if p.get("status") != "Approved"])

            try:
                kw_rows = backend.get_all_keywords() or []
                good_kw_list = [r["word"] for r in kw_rows if r.get("good")]
                bad_kw_list = [r["word"] for r in kw_rows if r.get("good") is False]
            except Exception:
                good_kw_list = []
                bad_kw_list = []

            st.subheader("Processed Results")
            st.write(f"Auto-approved: {approved_count} | Unknowns requiring review: {unknown_count}")

            with st.form("processed_review_form"):
                for i, p in enumerate(ordered_processed):
                    prefix = "✅ " if p.get("status") == "Approved" else ""
                    title = p.get("title") or p.get("title_english") or "No Title"
                    url = p.get("url") or "Unknown URL"

                    with st.expander(f"{prefix}{url} — {title}", expanded=(p.get("status") != "Approved")):
                        st.markdown(f"**Domain URL:** {p.get('url')}")
                        st.markdown(f"**Original URL:** {p.get('link')}")
                        st.markdown(f"**Source:** {p.get('source')}")
                        st.write(f"**Response Code:** `{p.get('response_code')}`")
                        st.markdown(f"**Original Title:** {p.get('title')}")
                        st.markdown(f"**English Title:** {p.get('title_english')}")
                        st.markdown(f"**Original Description:** {p.get('description')}")
                        st.markdown(f"**English Description:** {p.get('description_english')}")

                        text_fields = " ".join([str(p.get(k) or "") for k in ("title", "description", "title_english", "description_english")]).lower()
                        matched_good = [kw for kw in good_kw_list if kw.lower() in text_fields]
                        matched_bad = [kw for kw in bad_kw_list if kw.lower() in text_fields]

                        st.markdown(f"**Good keywords:** {', '.join(matched_good) if matched_good else 'None'}")
                        st.markdown(f"**Bad keywords:** {', '.join(matched_bad) if matched_bad else 'None'}")

                        default_idx = status_options.index(p.get("status")) if p.get("status") in status_options else 1

                        st.selectbox("Select Status:", options=status_options, index=default_idx, key=f"status_{i}")
                        st.text_input("Status Details:", value=p.get("status_details") or "", key=f"details_{i}")
                        langs_data = p.get("languages") or []
                        if isinstance(langs_data, dict):
                            langs_csv = ", ".join(list(langs_data.keys()))
                        else:
                            langs_csv = ", ".join(langs_data)
                        st.text_input("Languages (comma-separated):", value=langs_csv, key=f"langs_{i}")

                commit_all = st.form_submit_button("Commit All to Archive", key="commit_all_btn")

            if commit_all:
                to_commit = []

                for i, p in enumerate(ordered_processed):
                    updated_payload = p.copy()

                    status = st.session_state.get(f"status_{i}", updated_payload.get("status"))
                    details = st.session_state.get(f"details_{i}", updated_payload.get("status_details"))
                    default_langs = updated_payload.get("languages") or []
                    if isinstance(default_langs, dict):
                        default_langs = list(default_langs.keys())
                    langs_str = st.session_state.get(f"langs_{i}", ", ".join(default_langs))

                    cleaned_langs = [l.strip() for l in langs_str.split(",") if l.strip()]

                    previous_status = updated_payload.get("status")
                    updated_payload["status"] = status
                    updated_payload["status_details"] = resolve_status_details(
                        updated_payload["status"],
                        details,
                        updated_payload.get("status_details"),
                        status_changed=(status != previous_status),
                    )

                    if cleaned_langs:
                        updated_payload["languages"] = cleaned_langs
                    else:
                        updated_payload["languages"] = None

                    to_commit.append(updated_payload)

                if to_commit:
                    saved = 0
                    session_user = normalize_user_name(get_session_user())
                    if not session_user:
                        st.error("User must be one of: Hana, Roi, Yehonatan.")
                    else:
                        st.session_state["domain_user"] = session_user
                        for item in to_commit:
                            try:
                                item.pop("link", None)
                                item["user"] = session_user
                                backend.insert_domain(item)
                                saved += 1
                            except Exception as e:
                                st.error(f"Failed to save {item.get('url')}: {e}")

                        if saved:
                            st.success(f"Saved {saved} records to the archive.")
                            with st.spinner("Refreshing..."):
                                time.sleep(2)
                            clear_processed_review_state()
                            st.rerun()
                else:
                    st.info("No records to commit.")

    # --- VIEW 4A: ADD/MANAGE KEYWORDS ---
    elif option == "🏷️ Add Keywords":
        st.header("🏷️ Add Keywords to Archive")
        st.subheader("1. Ingest Raw Keywords")
        
        with st.form("kw_input_form"):
            raw_input = st.text_area("Keywords (separated with commas)", placeholder="e.g., חדשות, security, archive, ספורט")
            keyword_type = st.radio("Classification Evaluation:", ["Good Keyword (Whitelisted)", "Bad Keyword (Flagged)"], horizontal=True)
            analyze_kw_btn = st.form_submit_button("Add Keywords")

        if analyze_kw_btn:
            if not raw_input.strip():
                st.warning("Please supply words to analyze.")
            else:
                # Allow keywords separated by commas or newlines
                words_to_process = [w.strip() for w in re.split(r"[,\n]+", raw_input) if w.strip()]

                # Deduplicate by case-insensitive word to avoid ON CONFLICT multi-row errors
                unique = {}
                is_good = True if "Good" in keyword_type else False
                for word in words_to_process:
                    key = word.lower()
                    unique[key] = {"word": word, "good": is_good}

                payloads = list(unique.values())

                try:
                    res = backend.insert_final_keywords(payloads)
                    if res.get("success"):
                        st.success(f"Successfully processed and synced **{res.get('count', 0)}** keywords!")
                        with st.spinner("Refreshing..."):
                            time.sleep(2)
                        st.rerun()
                except Exception as k_err:
                    err_str = str(k_err)
                    # Fallback: try inserting entries one-by-one to avoid batch upsert conflict
                    saved = 0
                    for p in payloads:
                        try:
                            backend.insert_final_keywords([p])
                            saved += 1
                        except Exception:
                            continue

                    if saved:
                        st.success(f"Partially saved {saved} keywords (fallback individual inserts).")
                        with st.spinner("Refreshing..."):
                            time.sleep(2)
                        st.rerun()
                    else:
                        st.error(f"Failed to submit keywords: {k_err}")

    # --- VIEW 4B: VIEW KEYWORDS DATABASE ---
    elif option == "🏷️ Keywords Database Explorer":
        st.header("🏷️ Keywords Database Browser")
        st.subheader("Current Database Entries Reference")
        view_mode = st.radio("Filter Database View by Type:", ["Show All", "🟢 Good Keywords Only", "🔴 Bad Keywords Only"], horizontal=True)

        try:
            all_keywords = backend.get_all_keywords()
            if all_keywords:
                kw_df = pd.DataFrame(all_keywords)
                kw_df["evaluation"] = kw_df["good"].apply(lambda g: "🟢 Good" if g else "🔴 Bad")
                
                if "Good" in view_mode:
                    kw_df = kw_df[kw_df["good"] == True]
                elif "Bad" in view_mode:
                    kw_df = kw_df[kw_df["good"] == False]

                # Search / filter bar for the displayed keywords
                search_q = st.text_input("Filter keywords:", placeholder="Type to filter by word or evaluation")

                if search_q and isinstance(search_q, str) and search_q.strip():
                    mask = (
                        kw_df["word"].astype(str).str.contains(search_q, case=False, na=False)
                        | kw_df["evaluation"].astype(str).str.contains(search_q, case=False, na=False)
                    )
                    filtered_df = kw_df[mask].reset_index(drop=True)
                else:
                    filtered_df = kw_df.copy().reset_index(drop=True)

                # Render table with an inline Delete column (buttons inside the table)
                # Header
                hdr_id, hdr_word, hdr_eval, hdr_created, hdr_del = st.columns([1, 4, 2, 2, 1])
                hdr_id.markdown("**ID**")
                hdr_word.markdown("**Word**")
                hdr_eval.markdown("**Evaluation**")
                hdr_created.markdown("**Created At**")
                hdr_del.markdown("**Delete**")

                for _, row in filtered_df.reset_index(drop=True).iterrows():
                    kw_id = int(row["id"])
                    kw_word = row["word"]
                    kw_eval = row.get("evaluation", "")
                    kw_created = row.get("created_at", "")

                    col_id, col_word, col_eval, col_created, col_del = st.columns([1, 4, 2, 2, 1])
                    col_id.write(kw_id)
                    col_word.write(kw_word)
                    col_eval.write(kw_eval)
                    col_created.write(kw_created)

                    with col_del:
                        if st.button("Delete", key=f"delete_kw_{kw_id}"):
                            st.session_state["pending_delete"] = kw_id
                            st.session_state["pending_delete_word"] = kw_word

                        if st.session_state.get("pending_delete") == kw_id:
                            st.warning(f"Confirm deletion of keyword '{kw_word}' (ID {kw_id})")
                            c1, c2 = st.columns([1, 1])
                            with c1:
                                if st.button("Confirm", key=f"confirm_delete_{kw_id}"):
                                    try:
                                        backend.delete_keyword(kw_id)
                                        st.success(f"Deleted keyword {kw_id}: {kw_word}")
                                        st.session_state.pop("pending_delete", None)
                                        st.session_state.pop("pending_delete_word", None)
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Failed to delete {kw_id}: {e}")
                            with c2:
                                if st.button("Cancel", key=f"cancel_delete_{kw_id}"):
                                    st.session_state.pop("pending_delete", None)
                                    st.session_state.pop("pending_delete_word", None)
            else:
                st.info("No records matching this keyword context exist.")
        except Exception as read_err:
            st.error(f"Failed to query active rows: {read_err}")
                
    else:
        st.info("Please pick a tool system module option from the sidebar manager to map live data components.")
