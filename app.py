from PIL import Image
import streamlit as st
from src.chat_tutor import TutorSession
from src.highlighter import highlight_region

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

    image = Image.open("temp_image.png")

    # Default image

    if "selected_image" not in st.session_state:
        st.session_state.selected_image = "temp_image.png"

    col1, col2 = st.columns([1, 2])

    # ==================================
    # IMAGE PREVIEW
    # ==================================

    with col1:

        st.image(
            uploaded_file,
            caption="Uploaded Screenshot",
            width="stretch"
        )

        st.subheader("🎯 Select Region Center")

        x = st.number_input(
            "X Coordinate",
            min_value=0,
            max_value=image.width,
            value=image.width // 2
        )

        y = st.number_input(
            "Y Coordinate",
            min_value=0,
            max_value=image.height,
            value=image.height // 2
        )

        if st.button("Preview Crop"):

            crop_size = 300

            left = max(
                0,
                x - crop_size // 2
            )

            top = max(
                0,
                y - crop_size // 2
            )

            right = min(
                image.width,
                x + crop_size // 2
            )

            bottom = min(
                image.height,
                y + crop_size // 2
            )

            cropped = image.crop(
                (
                    left,
                    top,
                    right,
                    bottom
                )
            )

            cropped.save(
                "selected_region.png"
            )

            st.session_state.selected_image = (
                "selected_region.png"
            )

            st.image(
                cropped,
                caption="Selected Region"
            )

        st.caption(
            f"Current Image: {st.session_state.selected_image}"
        )

    # ==================================
    # EXPLANATION
    # ==================================

    with col2:

        if st.button("📚 Explain"):

            image_path = (
                st.session_state.selected_image
            )

            with st.spinner(
                "📚 Analyzing screenshot..."
            ):

                session = TutorSession(
                    image_path,
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

        if "highlighted_image" in st.session_state:

            st.subheader(
                "🎯 ClickTutor Focus Area"
            )

            st.image(
                st.session_state.highlighted_image,
                width="stretch"
            )

        st.divider()

        st.subheader("💬 Chat with ClickTutor")

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

        prompt = st.chat_input(
            "Ask a follow-up question..."
        )

        if prompt:

            st.session_state.messages.append(
                {
                    "role": "user",
                    "content": prompt
                }
            )

            with st.spinner(
                "🎓 ClickTutor is thinking..."
            ):

                answer = (
                    st.session_state.session.ask(
                        prompt
                    )
                )

                location = (
                    st.session_state.session
                    .get_visual_location(answer)
                )

                size = (
                    st.session_state.session
                    .get_size(answer)
                )

                if location:

                    highlighted_path = (
                        highlight_region(
                            st.session_state.session.image_path,
                            location,
                            size
                        )
                    )

                    st.session_state.highlighted_image = (
                        highlighted_path
                    )

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