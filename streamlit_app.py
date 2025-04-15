import streamlit as st
from src.pipeline import run_pipeline

st.set_page_config(page_title="Lunathink AI Summary", layout="centered")
st.title("🧠 Lunathink | Daily AI Summary Generator")

st.markdown("""
Enter your search topics (one per line), and receive a personalized research summary in your inbox!
""")

with st.form("summary_form"):
    name = st.text_input("Your Name", placeholder="Jane Doe")
    email = st.text_input("Your Email", placeholder="jane@example.com")
    search_queries = st.text_area("Search Queries (one per line)", height=150)
    submit = st.form_submit_button("Generate & Email Summary")

if submit:
    if not name or not email or not search_queries.strip():
        st.error("Please fill in all fields.")
    else:
        queries = [q.strip() for q in search_queries.splitlines() if q.strip()]
        with st.spinner("Working on it... this may take a few minutes."):
            try:
                result = run_pipeline(queries, name, email)
                st.success("✅ Summary generated and sent via email!")
            except Exception as e:
                st.error(f"❌ Something went wrong: {e}")
