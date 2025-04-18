import streamlit as st
import re
from src.pipeline import run_pipeline


def is_valid_email(email):
    pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    return re.match(pattern, email)

st.set_page_config(page_title="Lunathink AI Summary", layout="wide")
st.title("🧠 Lunathink | Daily AI Summary Generator")

st.markdown(
    """
Enter your search topics (one per line), and receive a personalized research summary in your inbox!
"""
)

# Two columns: result on the left, form on the right
left_col, right_col = st.columns([1, 2])  # Wider on the right

with left_col:
    with st.form("summary_form"):
        name = st.text_input("Your Name", placeholder="Jane Doe")
        email = st.text_input("Your Email", placeholder="jane@example.com")
        if email:
            if is_valid_email(email):
                st.success(f"✅ Valid email: {email}")
            else:
                st.error("❌ Invalid email address")
        profile = st.selectbox(
            "Select Research Profile",
            ["Business", "Engineer", "Researcher"]
        )
        search_queries = st.text_area("Search Queries (one per line)", height=150)
        submit = st.form_submit_button("Generate & Email Summary")

with right_col:
    if submit:
        if not name or not email or not search_queries.strip():
            st.error("Please fill in all fields.")
        else:
            queries = [q.strip() for q in search_queries.splitlines() if q.strip()]
            with st.spinner("Working on it... this may take a few minutes."):
                try:
                    result = run_pipeline(queries, name, email, profile)
                    st.markdown(result, unsafe_allow_html=True)
                    st.success("✅ Summary generated and sent via email!")
                except Exception as e:
                    st.error(f"❌ Something went wrong: {e}")

