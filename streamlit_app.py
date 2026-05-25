import streamlit as st
from streamlit_option_menu import option_menu

# Initialize app options and authentication flag
apps = {}

# Sidebar Header
with st.sidebar:
    st.header("IIA Tools")

    apps = {
        "Search by Keywords": keywords.run if callable(keywords_tool.run) else None
    }
    apps = {k: v for k, v in apps.items() if v}  # Filter out invalid entries

    # Sidebar menu
    with st.sidebar:
        selected_app_name = option_menu(
            "Tools Menu",
            options=list(apps.keys()),
            icons=["search", "filter", "link", "database"],  # Customize icons
            menu_icon="tools",
            default_index=0,
            orientation="vertical"  # Sidebar menu
        )

    # Render the selected app
    app_function = apps[selected_app_name]
    if callable(app_function):
        st.title(selected_app_name)
        app_function(client)  # Pass the client to the app function
    else:
        st.error(f"The app '{selected_app_name}' is not callable.")
