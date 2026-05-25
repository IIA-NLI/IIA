import streamlit as st
from backend import ArchiveBackend

# --- PAGE SETUP ---
st.set_page_config(
    page_title="Israeli Internet Archive Database Manager",
    page_icon="🌐",
    layout="wide",
)


# --- INSTANTIATE BACKEND MODULE ---
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
        st.text_input(
            "Please enter your access password",
            type="password",
            on_change=password_entered,
            key="password",
        )
        return False
    elif not st.session_state["password_correct"]:
        st.text_input(
            "Please enter your access password",
            type="password",
            on_change=password_entered,
            key="password",
        )
        st.error("😕 Password incorrect")
        return False
    else:
        return True


# --- APP ROUTING ---
if check_password():
    st.title("🇮🇱 Israeli Internet Archive Management Portal")

    option = st.sidebar.radio(
        "Navigation Menu", ["Add URL Manually", "Keyword Search Interface"]
    )

    # --- VIEW 1: MANUAL INGESTION ---
    if option == "Add URL Manually":
        st.header("📝 Manual URL Extraction and Ingestion")

        with st.form("add_url_form"):
            input_url = st.text_input("Target URL", placeholder="example.co.il")
            submit_btn = st.form_submit_button("Process and Add to Archive")

        if submit_btn:
            if not input_url:
                st.warning("Please specify a valid URL path first.")
            else:
                with st.spinner("Analyzing and parsing remote server contents..."):
                    # Call Backend Service functions
                    payload = backend.analyze_and_extract_url(input_url)

                    try:
                        backend.insert_domain(payload)
                        st.success(
                            f"Successfully registered **{payload['url']}** to the Database!"
                        )

                        # UI Layout splits
                        col1, col2 = st.columns(2)
                        with col1:
                            st.metric(
                                label="Server Response Code",
                                value=payload["response_code"] or "N/A",
                            )
                            st.text_input(
                                "Extracted Title", value=payload["title"], disabled=True
                            )
                            st.text_area(
                                "Extracted Description",
                                value=payload["description"],
                                disabled=True,
                            )

                        with col2:
                            st.write("**Languages Map:**", payload["languages"])
                            st.text_input(
                                "English Title Translation",
                                value=payload["title_english"],
                                disabled=True,
                            )
                            st.text_area(
                                "English Description Translation",
                                value=payload["description_english"],
                                disabled=True,
                            )

                    except Exception as db_err:
                        st.error(f"Database sync failed: {db_err}")

    # --- VIEW 2: KEYWORD SEARCH ---
    elif option == "Keyword Search Interface":
        st.header("🔍 Archive Keyword Query Engine")

        search_query = st.text_input(
            "Search by keyword across titles, URLs, descriptions, or translations:",
            placeholder="e.g., חדשות, security, archive",
        )

        if search_query:
            with st.spinner("Searching records..."):
                try:
                    records = backend.search_domains(search_query)

                    if records:
                        st.write(f"Found **{len(records)}** matching records.")

                        for row in records:
                            header_title = (
                                row["title"]
                                or row["title_english"]
                                or "No Title Available"
                            )
                            with st.expander(f"🌐 {row['url']} — {header_title}"):
                                col1, col2 = st.columns(2)
                                with col1:
                                    st.write(f"**Original Title:** {row['title']}")
                                    st.write(
                                        f"**Original Description:** {row['description']}"
                                    )
                                    st.write(
                                        f"**Response Code:** `{row['response_code']}` | **Status:** `{row['status']}`"
                                    )
                                with col2:
                                    st.write(
                                        f"**English Title:** {row['title_english']}"
                                    )
                                    st.write(
                                        f"**English Description:** {row['description_english']}"
                                    )
                                    st.write(
                                        f"**Languages Map:** `{row['languages']}`"
                                    )

                                st.caption(
                                    f"Record Source: `{row['source']}` | Last Updated: {row['updated_at']}"
                                )
                    else:
                        st.info("No documents found matching that query parameter.")

                except Exception as search_err:
                    st.error(f"Search query execution failed: {search_err}")
        else:
            st.info("Provide a keyword phrase string above to fetch indexed data.")
