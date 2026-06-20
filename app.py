import streamlit as st
from src.chat_tutor import TutorSession

# ==================================
# PAGE CONFIG
# ==================================

st.set_page_config(
    page_title="ClickTutor",
    page_icon="🎓",
    layout="wide"
)

st.title("🎓 ClickTutor")
st.caption("Your AI-powered learning companion")

# ==================================
# SIDEBAR
# ==================================

with st.sidebar:

    st.header("⚙️ Settings")

    mode = st.selectbox(
        "Tutor Mode",
        [
            "student",
            "dsa",
            "exam"
        ]
    )

    st.success(
        f"Current Mode: {mode.upper()}"
    )

    uploaded_file = st.file_uploader(
        "Upload Screenshot",
        type=["png", "jpg", "jpeg"]
    )

    if st.button("🗑 New Session"):

        st.session_state.clear()
        st.rerun()

# ==================================
# MAIN APP
# ==================================

if uploaded_file:

    # Save uploaded image
    with open("temp_image.png", "wb") as f:
        f.write(uploaded_file.getbuffer())

    col1, col2 = st.columns([1, 2])

    # ==================================
    # IMAGE PREVIEW
    # ==================================

    with col1:

        st.image(
            uploaded_file,
            caption="Uploaded Screenshot",
            use_container_width=True
        )

    # ==================================
    # EXPLANATION
    # ==================================

    with col2:

        if st.button("📚 Explain"):

            with st.spinner(
                "📚 Analyzing screenshot..."
            ):

                session = TutorSession(
                    "temp_image.png",
                    mode=mode
                )

            st.session_state.session = session
            st.session_state.messages = []

        if "session" in st.session_state:

            st.subheader("📖 Explanation")

            with st.container(border=True):

                st.markdown(
                    st.session_state.session.explanation
                )

    # ==================================
    # CHAT SECTION
    # ==================================

    if "session" in st.session_state:

        st.divider()

        st.subheader("💬 Chat with ClickTutor")

        # Display conversation

        for msg in st.session_state.messages:

            if msg["role"] == "user":

                with st.chat_message(
                    "user",
                    avatar="🧑"
                ):

                    st.markdown(
                        msg["content"]
                    )

            else:

                with st.chat_message(
                    "assistant",
                    avatar="🎓"
                ):

                    st.markdown(
                        msg["content"]
                    )

        # Chat input

        prompt = st.chat_input(
            "Ask a follow-up question..."
        )

        if prompt:

            # Add user message

            st.session_state.messages.append(
                {
                    "role": "user",
                    "content": prompt
                }
            )

            # Generate answer

            with st.spinner(
                "🎓 ClickTutor is thinking..."
            ):

                answer = (
                    st.session_state.session.ask(
                        prompt
                    )
                )

            # Add assistant message

            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": answer
                }
            )

            st.rerun()

else:

    st.info(
        "👈 Upload a screenshot from the sidebar to begin."
    )
