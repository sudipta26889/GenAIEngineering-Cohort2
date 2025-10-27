import streamlit as st
import asyncio
import time
import nest_asyncio

from blog_pipeline import (
    get_medium_output,
    select_ideas,
    pick_next_idea,
    research_agent,
    blog_writer_agent,
    blog_review_agent,
    social_media_agent,
    ghost_drafter_agent,
    BlogState,
)

nest_asyncio.apply()
st.set_page_config(layout="wide")
st.title("🚀 AI Agent Dashboard")

steps = ["initial_ideas", "select_ideas", "pick_next", "research", "write", "review", "social_media", "draft"]

# === Session State Setup ===
if "status" not in st.session_state:
    st.session_state.status = {step: "⏳ Pending" for step in steps}
if "outputs" not in st.session_state:
    st.session_state.outputs = {step: "" for step in steps}
if "initial_state" not in st.session_state:
    st.session_state.initial_state = None
if "ghost_done" not in st.session_state:
    st.session_state.ghost_done = False
if "pipeline_started" not in st.session_state:
    st.session_state.pipeline_started = False
if "idea_loop_active" not in st.session_state:
    st.session_state.idea_loop_active = False

# === 💄 UI Style Enhancements ===
st.markdown("""
<style>
.card {
    background-color: #f9f9f9;
    padding: 1rem;
    border-radius: 15px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    margin-bottom: 1rem;
}
.status {
    font-weight: bold;
    padding: 0.3rem 0.6rem;
    border-radius: 8px;
    display: inline-block;
}
.status-pending { background-color: #eee; color: #555; }
.status-running { background-color: #fff3cd; color: #856404; }
.status-done    { background-color: #d4edda; color: #155724; }
.status-failed  { background-color: #f8d7da; color: #721c24; }
</style>
""", unsafe_allow_html=True)

# === UI Rendering (Styled) ===
cols = st.columns(len(steps))
for i, step in enumerate(steps):
    status = st.session_state.status[step]
    if "✅" in status:
        status_class = "status-done"
    elif "🟡" in status:
        status_class = "status-running"
    elif "❌" in status:
        status_class = "status-failed"
    else:
        status_class = "status-pending"

    with cols[i]:
        st.markdown(f"<div class='card'>", unsafe_allow_html=True)
        st.markdown(f"<h5>{step.replace('_', ' ').title()}</h5>", unsafe_allow_html=True)
        st.markdown(f"<div class='status {status_class}'>{status}</div>", unsafe_allow_html=True)
        st.text_area("Output", st.session_state.outputs[step], height=300, key=f"out_{step}")
        st.markdown("</div>", unsafe_allow_html=True)

# === Main Async Pipeline ===
async def run_pipeline():
    state = st.session_state.initial_state

    if st.session_state.status["initial_ideas"] == "⏳ Pending":
        st.session_state.status["initial_ideas"] = "🟡 Running..."
        medium_raw = await get_medium_output()
        if not medium_raw:
            st.session_state.status["initial_ideas"] = "❌ Failed"
            st.session_state.outputs["initial_ideas"] = "Failed to fetch Medium ideas."
            st.rerun()
            return
        ideas = medium_raw.strip().split("\n")
        st.session_state.outputs["initial_ideas"] = "\n".join(ideas)
        st.session_state.status["initial_ideas"] = "✅ Done"
        st.session_state.initial_state = {
            "ideas": ideas,
            "selected_ideas": [],
            "current_idea": None,
            "research_data": None,
            "blog_post": None,
            "reddit_post": None,
            "hackernews_post": None,
            "completed_blogs": {}
        }
        st.rerun()
        return

    if st.session_state.status["select_ideas"] == "⏳ Pending":
        st.session_state.status["select_ideas"] = "🟡 Running..."
        state = select_ideas(st.session_state.initial_state)

        # ✅ Show cleaned display if available
        if "display_output" in state:
            st.session_state.outputs["select_ideas"] = state["display_output"]
        else:
            st.session_state.outputs["select_ideas"] = "\n".join(state["selected_ideas"])

        st.session_state.status["select_ideas"] = "✅ Done"
        st.session_state.initial_state = state
        st.session_state.idea_loop_active = True
        st.rerun()
        return

    if st.session_state.idea_loop_active and st.session_state.initial_state["selected_ideas"]:
        state = st.session_state.initial_state

        if st.session_state.status["pick_next"] != "✅ Done":
            st.session_state.status["pick_next"] = "🟡 Running..."
            state = pick_next_idea(state)
            st.session_state.outputs["pick_next"] = f"Idea picked: {state['current_idea']}"
            st.session_state.status["pick_next"] = "✅ Done"
            st.session_state.initial_state = state
            st.rerun()
            return

        if st.session_state.status["research"] != "✅ Done":
            st.session_state.status["research"] = "🟡 Running..."
            state = research_agent(state)
            if isinstance(state["research_data"], bytes):
                state["research_data"] = state["research_data"].decode("utf-8")
            st.session_state.outputs["research"] = state["research_data"][:1000] + "..."
            st.session_state.status["research"] = "✅ Done"
            st.session_state.initial_state = state
            st.rerun()
            return

        if st.session_state.status["write"] != "✅ Done":
            st.session_state.status["write"] = "🟡 Running..."
            state = blog_writer_agent(state)
            st.session_state.outputs["write"] = state["blog_post"][:1000] + "..."
            st.session_state.status["write"] = "✅ Done"
            st.session_state.initial_state = state
            st.rerun()
            return

        if st.session_state.status["review"] != "✅ Done":
            st.session_state.status["review"] = "🟡 Running..."
            state = blog_review_agent(state)
            review_results = state.get("review_results", {})
            overall_score = review_results.get("overall_score", "N/A")
            total_issues = (
                len(review_results.get("grammar_issues", [])) +
                len(review_results.get("technical_issues", [])) +
                len(review_results.get("structure_issues", [])) +
                len(review_results.get("factual_issues", [])) +
                len(review_results.get("style_issues", [])) +
                len(review_results.get("code_issues", []))
            )
            review_summary = f"Score: {overall_score}/10\nTotal Issues Found: {total_issues}\n"
            if review_results.get("suggestions"):
                review_summary += f"Key Suggestions: {len(review_results['suggestions'])} items\n"
            if review_results.get("needs_major_revision") == "true":
                review_summary += "⚠️ Major revision recommended"
            else:
                review_summary += "✅ Minor corrections applied"

            st.session_state.outputs["review"] = review_summary
            st.session_state.status["review"] = "✅ Done"
            st.session_state.initial_state = state
            st.rerun()
            return

        if st.session_state.status["social_media"] != "✅ Done":
            st.session_state.status["social_media"] = "🟡 Running..."
            state = social_media_agent(state)
            reddit_preview = state["reddit_post"][:500] + "..." if state["reddit_post"] else "Failed"
            hn_preview = state["hackernews_post"][:500] + "..." if state["hackernews_post"] else "Failed"
            st.session_state.outputs["social_media"] = f"REDDIT POST:\n{reddit_preview}\n\nHACKER NEWS POST:\n{hn_preview}"
            st.session_state.status["social_media"] = "✅ Done"
            st.session_state.initial_state = state
            st.rerun()
            return

        if not st.session_state.ghost_done:
            st.session_state.status["draft"] = "📤 Publishing to Ghost..."
            state = await ghost_drafter_agent(state)

            st.toast(f"✅ Blog completed: {state.get('current_idea', 'Unknown')}", icon="✍️")
            st.session_state.initial_state = state
            st.session_state.ghost_done = True
            st.rerun()
            return

        # Reset statuses for next idea loop
        st.session_state.status["pick_next"] = "⏳ Pending"
        st.session_state.status["research"] = "⏳ Pending"
        st.session_state.status["write"] = "⏳ Pending"
        st.session_state.status["review"] = "⏳ Pending"
        st.session_state.status["social_media"] = "⏳ Pending"
        st.session_state.status["draft"] = "⏳ Pending"
        st.session_state.ghost_done = False
        st.rerun()
        return

# === Start Button ===
if st.button("🚀 Start Agent"):
    st.session_state.status = {step: "⏳ Pending" for step in steps}
    st.session_state.outputs = {step: "" for step in steps}
    st.session_state.ghost_done = False
    st.session_state.pipeline_started = True
    st.session_state.idea_loop_active = False
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(run_pipeline())
    else:
        loop.run_until_complete(run_pipeline())

# === Auto Continue if Pipeline Started ===
if st.session_state.pipeline_started:
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(run_pipeline())
    else:
        loop.run_until_complete(run_pipeline())
