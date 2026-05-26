import streamlit as st
import pandas as pd
from backend import ArchiveBackend

# --- PAGE SETUP ---
st.set_page_config(
    page_title="Israeli Internet Archive Database Manager",
    page_icon="🌐",
    layout="wide",
)


@st.cache_resource
def get_backend():
    return ArchiveBackend()


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
    
    # Combined functional routing map into a single organized radio menu with groupings
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
            fetch_btn = st.form_submit_button("Analyze Remote Server")

        if fetch_btn:
            if not input_url:
                st.warning("Please specify a valid URL path first.")
            else:
                with st.spinner("Analyzing and parsing remote server contents..."):
                    scraped_payload = backend.analyze_and_extract_url(input_url)
                    st.session_state["current_payload"] = scraped_payload
                    st.session_state["show_editor"] = True

        if st.session_state.get("show_editor"):
            payload = st.session_state["current_payload"]

            st.write("---")
            st.subheader("🔍 Review and Edit Extracted Metadata")

            with st.form("metadata_edit_form"):
                col1, col2 = st.columns(2)

                with col1:
                    st.metric(label="Server Response Code", value=payload["response_code"] or "N/A")
                    
                    status_options = ["Approved", "Not sure", "Not relevant"]
                    default_status_idx = status_options.index(payload["status"]) if payload["status"] in status_options else 0
                    edited_status = st.selectbox("Status Evaluation", options=status_options, index=default_status_idx)
                    edited_status_details = st.text_input("Status Details", value="Manually added")

                    st.text_input("Extracted Title", value=payload["title"], disabled=True)
                    st.text_area("Extracted Description", value=payload["description"], disabled=True)

                with col2:
                    detected_langs_list = list(payload["languages"].keys())
                    edited_langs_str = st.text_input("Languages List (comma-separated)", value=", ".join(detected_langs_list))

                    st.text_input("English Title Translation", value=payload["title_english"], disabled=True)
                    st.text_area("English Description Translation", value=payload["description_english"], disabled=True)

                submit_to_db = st.form_submit_button("Save and Commit to Archive")

                if submit_to_db:
                    cleaned_langs = [l.strip() for l in edited_langs_str.split(",") if l.strip()]
                    updated_langs_dict = {lang: 1.0 for lang in cleaned_langs}

                    payload["status"] = edited_status
                    payload["status_details"] = edited_status_details
                    payload["languages"] = updated_langs_dict

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
                for row in results:
                    header_title = row.get("title") or row.get("title_english") or "No Title Available"
                    with st.expander(f"🌐 {row['url']} — {header_title}"):
                        col1, col2 = st.columns(2)
                        with col1:
                            st.write(f"**Original Title:** {row.get('title')}")
                            st.write(f"**Original Description:** {row.get('description')}")
                            st.write(f"**Response Code:** `{row.get('response_code')}` | **Status:** `{row.get('status')}`")
                        with col2:
                            st.write(f"**English Title:** {row.get('title_english')}")
                            st.write(f"**English Description:** {row.get('description_english')}")
                            st.write(f"**Languages Map:** `{row.get('languages')}`")
                        st.caption(f"Source: `{row.get('source')}` | Updated: {row.get('updated_at')}")

    # --- VIEW 3: LEGACY REGULAR SEARCH BACKWARD-COMPATIBILITY ---
    elif option == "🌐 Add URLs using Search":
        st.header("🔍 Quick Query Search Engine")
        search_query = st.text_input("Search across rows instantly:", placeholder="e.g., archive")
        if search_query:
            with st.spinner("Searching records..."):
                try:
                    records = backend.search_domains(search_query)
                    if records:
                        st.write(f"Found **{len(records)}** records.")
                        for row in records:
                            h = row["title"] or row["title_english"] or "No Title"
                            with st.expander(f"🌐 {row['url']} — {h}"):
                                c1, c2 = st.columns(2)
                                with c1:
                                    st.write(f"**Title:** {row['title']}")
                                    st.write(f"**Description:** {row['description']}")
                                with c2:
                                    st.write(f"**English Title:** {row['title_english']}")
                                    st.write(f"**Languages Map:** `{row['languages']}`")
                    else:
                        st.info("No matching records found.")
                except Exception as search_err:
                    st.error(f"Search query execution failed: {search_err}")

    # --- VIEW 4A: ADD/MANAGE KEYWORDS ---
    elif option == "🏷️ Add Keywords":
        st.header("🏷️ Add Keywords to Archive")

        st.subheader("1. Ingest Raw Keywords")
        with st.form("kw_input_form"):
            raw_input = st.text_area(
                "Keywords (separated with commas)",
                placeholder="e.g., חדשות, security, archive, ספורט",
            )
            keyword_type = st.radio(
                "Classification Evaluation:",
                ["Good Keyword (Whitelisted)", "Bad Keyword (Flagged)"],
                horizontal=True
            )
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
            st.info("Modify languages below (comma-separated) before clicking save.")

            with st.form("kw_edit_form"):
                final_payloads = []
                
                for idx, item in enumerate(st.session_state["analyzed_keywords_batch"]):
                    st.markdown(f"**Word:** `{item['word']}`")
                    edited_lang_csv = st.text_input(
                        f"Languages for '{item['word']}'", 
                        value=", ".join(item["languages_list"]), 
                        key=f"kw_lang_{idx}"
                    )
                    
                    cleaned_langs = [l.strip() for l in edited_lang_csv.split(",") if l.strip()]
                    langs_dict = {lang: 1.0 for lang in cleaned_langs}
                    
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
                
    # --- HANDLING SYSTEM SEPARATOR RELEASES ---
    else:
        st.info("Please pick a tool system module option from the sidebar manager to map live data components.")