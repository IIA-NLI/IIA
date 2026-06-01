import streamlit as st
import pandas as pd
from backend import ArchiveBackend
from urllib.parse import urlparse
import time

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


# --- AUTHENTICATION SHIELD ---
def check_password():
    """Returns True if the user entered the correct password."""
    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Please enter your access password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Please enter your access password", type="password", on_change=password_entered, key="password")
        st.error("😕 Password incorrect")
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
            "🌐 Add a URL Manually", 
            "🌐 Domains Database Explorer", 
            "🏷️ Add Keywords",
            "🏷️ Keywords Database Explorer"
        ]
    )

    # --- VIEW 1: MANUAL URL INGESTION ---
    if option == "🌐 Add a URL Manually":
        st.header("📝 Manual URL Extraction and Ingestion")

        with st.form("url_fetch_form"):
            input_url = st.text_input("Target URL", placeholder="example.co.il")
            fetch_btn = st.form_submit_button("Analyze Domain")

        if fetch_btn:
            # Explicit input verification: Check format, whitespace, or illegal chars
            if not input_url:
                st.warning("Please specify a valid URL path first.")
            elif not backend.is_valid_url_format(input_url):
                st.error("❌ Invalid URL structure! Spaces or special characters are forbidden.")
            else:
                normalized_url, domain_key, has_extra_path, trimmed_url = backend.normalize_manual_url(input_url)

                if has_extra_path:
                    st.session_state["manual_original_url"] = normalized_url
                    st.session_state["manual_trimmed_url"] = trimmed_url
                    st.session_state["manual_pending_domain"] = domain_key
                    st.session_state["manual_url_needs_choice"] = True
                    st.session_state["manual_domain_exists"] = False
                    st.session_state["show_editor"] = False
                else:
                    st.session_state["manual_url_needs_choice"] = False
                    st.session_state["manual_pending_url"] = normalized_url
                    st.session_state["manual_pending_domain"] = domain_key
                    if backend.domain_exists(domain_key):
                        st.session_state["manual_domain_exists"] = True
                        st.session_state["show_editor"] = False
                    else:
                        with st.spinner("Analyzing and parsing remote server contents..."):
                            scraped_payload = backend.analyze_and_extract_url(normalized_url)
                            st.session_state["current_payload"] = scraped_payload
                            st.session_state["show_editor"] = True
                            st.session_state["manual_domain_exists"] = False

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
                        st.session_state["current_payload"] = scraped_payload
                        st.session_state["show_editor"] = True
                else:
                    st.info("Skipped analysis for the existing domain.")
                    st.session_state["show_editor"] = False

                st.session_state["manual_domain_exists"] = False

        if st.session_state.get("show_editor"):
            payload = st.session_state["current_payload"]
            chosen_url = st.session_state.get("manual_pending_url", payload.get("url"))

            st.write("---")
            st.subheader("🔍 Review and Edit Extracted Metadata")
            st.markdown(f"**Chosen URL:** `{chosen_url}`")

            with st.form("metadata_edit_form"):
                col1, col2 = st.columns(2)

                with col1:
                    st.metric(label="Server Response Code", value=payload["response_code"] or "N/A")
                    
                    status_options = ["Approved", "Not sure", "Not relevant"]
                    default_status_idx = status_options.index(payload["status"]) if payload["status"] in status_options else 0
                    edited_status = st.selectbox("Status Evaluation", options=status_options, index=default_status_idx)
                    edited_status_details = st.text_input("Status Details", value="Manually added")

                    st.text_input("Extracted Title", value=payload["title"] or "", disabled=True)
                    st.text_area("Extracted Description", value=payload["description"] or "", disabled=True)

                with col2:
                    detected_langs_list = (
                        list(payload["languages"].keys())
                        if isinstance(payload.get("languages"), dict)
                        else []
                    )
                    edited_langs_str = st.text_input("Languages List (comma-separated)", value=", ".join(detected_langs_list))

                    st.text_input("English Title Translation", value=payload["title_english"] or "", disabled=True)
                    st.text_area("English Description Translation", value=payload["description_english"] or "", disabled=True)

                submit_to_db = st.form_submit_button("Save and Commit to Archive")

                if submit_to_db:
                    cleaned_langs = [l.strip() for l in edited_langs_str.split(",") if l.strip()]

                    payload["status"] = edited_status
                    payload["status_details"] = edited_status_details
                    payload["languages"] = (
                        {lang: 1.0 for lang in cleaned_langs}
                        if cleaned_langs
                        else None
                    )

                    try:
                        backend.insert_domain(payload)
                        st.success(f"Successfully registered **{payload['url']}** to the Database!")
                        st.session_state["show_editor"] = False
                    except Exception as db_err:
                        st.error(f"Database sync failed: {db_err}")

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
                        ordered_cols = ["id", "url", "status", "status_details", "response_code", "title", "title_english", "languages", "source", "updated_at"]
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
                                langs_csv = ", ".join(langs_value.keys()) if isinstance(langs_value, dict) else str(langs_value)
                                edited_langs = st.text_input("Languages (comma-separated):", value=langs_csv)

                                save_btn = st.form_submit_button("Save changes")
                                cancel_btn = st.form_submit_button(
                                    "Cancel",
                                    on_click=lambda k=edit_key: st.session_state.__setitem__(k, False),
                                )

                                if save_btn:
                                    updated_row = row.copy()
                                    updated_row["status"] = edited_status
                                    updated_row["status_details"] = edited_details or updated_row.get("status_details")
                                    updated_row["title"] = edited_title or None
                                    updated_row["description"] = edited_description or None
                                    updated_row["title_english"] = edited_title_en or None
                                    updated_row["description_english"] = edited_description_en or None
                                    cleaned_langs = [l.strip() for l in edited_langs.split(",") if l.strip()]
                                    if cleaned_langs:
                                        updated_row["languages"] = {lang: 1.0 for lang in cleaned_langs}
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

            col3, col4 = st.columns(2)
            with col3:
                include_inurl = st.checkbox("Include 'inurl' in the search", value=False)
            with col4:
                homepage_only = st.checkbox("Include only homepage results", value=False)

            run_search = st.form_submit_button("Run Keyword Search")

        if run_search:
            clear_processed_review_state()

            if not keywords_query.strip():
                st.warning("Please provide keywords to search for.")
            else:
                keywords = [k.strip() for k in keywords_query.split(",") if k.strip()]
                all_links = []  

                for kw in keywords:
                    # 'inurl' option implementation
                    q = f"inurl:{kw}" if include_inurl else kw
                    try:
                        links = backend.google_search(q, num_results=limit, language=language)
                    except Exception as e:
                        st.error(f"Search failed for '{kw}': {e}")
                        links = []
                    all_links.extend([(l, kw) for l in links])

                domain_to_keyword = {}
                root_urls = []
                seen = set()

                for link, kw in all_links:
                    raw = link
                    if not raw:
                        continue
                    if not raw.startswith("http://") and not raw.startswith("https://"):
                        raw = "http://" + raw

                    p = urlparse(raw)
                    netloc = (p.netloc or "").lower()
                    if not netloc:
                        continue

                    domain_key = netloc[4:] if netloc.startswith("www.") else netloc
                    domain_root = f"{p.scheme}://{p.netloc}"

                    # 'homepage_only' filtering implementation
                    if homepage_only:
                        path_clean = p.path.strip("/")
                        # If a path query or fragment parameters exist, reject this non-homepage branch result
                        if path_clean or p.query or p.fragment:
                            continue

                    if domain_key in seen:
                        continue

                    try:
                        if backend.domain_exists(domain_key):
                            continue
                    except Exception:
                        pass

                    seen.add(domain_key)
                    root_urls.append(domain_root)
                    domain_to_keyword[domain_root] = kw

                unique_links = root_urls
                st.success(f"Collected {len(unique_links)} unique domain roots from search results.")

                try:
                    kw_rows = backend.get_all_keywords() or []
                    good_kw_list = [r["word"] for r in kw_rows if r.get("good")]
                except Exception:
                    good_kw_list = []

                processed = []

                for link in unique_links:
                    originating_kw = domain_to_keyword.get(link)

                    try:
                        payload = backend.analyze_and_extract_url(link)
                    except Exception as e:
                        st.error(f"Failed to analyze {link}: {e}")
                        continue

                    if originating_kw:
                        payload["source"] = f"Google search for {originating_kw}"

                    try:
                        payload = backend.evaluate_payload(payload, good_keywords=good_kw_list)
                    except Exception:
                        approved = False
                        parsed = urlparse(payload.get("url") or link)
                        hostname = parsed.hostname or ""

                        if hostname.lower().endswith(".il"):
                            approved = True

                        languages = payload.get("languages") or {}
                        if any("hebrew" in str(l).lower() or str(l).lower().startswith("he") for l in languages.keys()):
                            approved = True

                        text_fields = " ".join([str(payload.get(k) or "") for k in ("title", "description", "title_english", "description_english")]).lower()
                        for gkw in good_kw_list:
                            if gkw.lower() in text_fields:
                                approved = True
                                break

                        payload["status"] = "Approved" if approved else "Not sure"
                        payload["status_details"] = "Auto-evaluated via keyword search"

                    processed.append(payload)

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

            st.subheader("Processed Results")
            st.write(f"Auto-approved: {approved_count} | Unknowns requiring review: {unknown_count}")

            with st.form("processed_review_form"):
                for i, p in enumerate(ordered_processed):
                    prefix = "✅ " if p.get("status") == "Approved" else ""
                    title = p.get("title") or p.get("title_english") or "No Title"
                    url = p.get("url") or "Unknown URL"

                    with st.expander(f"{prefix}{url} — {title}", expanded=(p.get("status") != "Approved")):
                        st.markdown(f"**Source:** {p.get('source')}")
                        st.write(f"**Response Code:** `{p.get('response_code')}`")
                        st.markdown(f"**Original Title:** {p.get('title')}")
                        st.markdown(f"**English Title:** {p.get('title_english')}")
                        st.markdown(f"**Original Description:** {p.get('description')}")
                        st.markdown(f"**English Description:** {p.get('description_english')}")

                        default_idx = status_options.index(p.get("status")) if p.get("status") in status_options else 1

                        st.selectbox("Select Status:", options=status_options, index=default_idx, key=f"status_{i}")
                        st.text_input("Status Details:", value=p.get("status_details") or "", key=f"details_{i}")
                        langs_csv = ", ".join(list((p.get("languages") or {}).keys()))
                        st.text_input("Languages (comma-separated):", value=langs_csv, key=f"langs_{i}")

                commit_all = st.form_submit_button("Commit All to Archive", key="commit_all_btn")

            if commit_all:
                to_commit = []

                for i, p in enumerate(ordered_processed):
                    updated_payload = p.copy()

                    status = st.session_state.get(f"status_{i}", updated_payload.get("status"))
                    details = st.session_state.get(f"details_{i}", updated_payload.get("status_details"))
                    langs_str = st.session_state.get(f"langs_{i}", ", ".join(list((updated_payload.get("languages") or {}).keys())))

                    cleaned_langs = [l.strip() for l in langs_str.split(",") if l.strip()]

                    updated_payload["status"] = status
                    if updated_payload["status"] == "Approved":
                        updated_payload["status_details"] = details or "Determined by user"
                    else:
                        updated_payload["status_details"] = details or updated_payload.get("status_details")

                    if cleaned_langs:
                        updated_payload["languages"] = {lang: 1.0 for lang in cleaned_langs}
                    else:
                        updated_payload["languages"] = None

                    to_commit.append(updated_payload)

                if to_commit:
                    saved = 0
                    for item in to_commit:
                        try:
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
            analyze_kw_btn = st.form_submit_button("Analyze Keywords")

        if analyze_kw_btn:
            if not raw_input.strip():
                st.warning("Please supply words to analyze.")
            else:
                words_to_process = [w.strip() for w in raw_input.split(",") if w.strip()]
                st.session_state["analyzed_keywords_batch"] = backend.analyze_keywords_before_saving(words_to_process)
                st.session_state["is_good_kw_type"] = True if "Good" in keyword_type else False
                st.session_state["show_kw_editor"] = True

        if st.session_state.get("show_kw_editor"):
            st.write("---")
            st.subheader("2. Review & Edit Detected Languages")

            with st.form("kw_edit_form"):
                final_payloads = []
                for idx, item in enumerate(st.session_state["analyzed_keywords_batch"]):
                    st.markdown(f"**Word:** `{item['word']}`")
                    edited_lang_csv = st.text_input(f"Languages for '{item['word']}'", value=", ".join(item["languages_list"]), key=f"kw_lang_{idx}")
                    
                    cleaned_langs = [l.strip() for l in edited_lang_csv.split(",") if l.strip()]
                    langs_dict = {lang: 1.0 for lang in cleaned_langs} if cleaned_langs else None
                    
                    final_payloads.append({
                        "word": item["word"],
                        "good": st.session_state["is_good_kw_type"],
                        "language": langs_dict
                    })

                commit_kws = st.form_submit_button("Commit Finalized Keywords to DB")

                if commit_kws:
                    try:
                        res = backend.insert_final_keywords(final_payloads)
                        if res["success"]:
                            st.success(f"Successfully processed and synced **{res['count']}** keywords!")
                            st.session_state["show_kw_editor"] = False
                            del st.session_state["analyzed_keywords_batch"]
                            st.rerun()
                    except Exception as k_err:
                        st.error(f"Failed to submit configuration: {k_err}")

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

                display_df = kw_df[["id", "word", "evaluation", "language", "created_at"]]
                st.dataframe(display_df, use_container_width=True, hide_index=True)
                
                with st.expander("🗑️ Delete Database Keyword Entry"):
                    remove_id = st.number_input("Target Keyword ID to Remove", step=1, value=0)
                    remove_btn = st.button("Delete Selected ID Key", type="secondary")
                    if remove_btn and remove_id > 0:
                        backend.delete_keyword(int(remove_id))
                        st.success(f"Row item reference {remove_id} deleted successfully.")
                        st.rerun()
            else:
                st.info("No records matching this keyword context exist.")
        except Exception as read_err:
            st.error(f"Failed to query active rows: {read_err}")
                
    else:
        st.info("Please pick a tool system module option from the sidebar manager to map live data components.")