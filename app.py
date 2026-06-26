from pathlib import Path
from uuid import uuid4

from PIL import Image
import streamlit as st
from src.chat_tutor import TutorSession


SESSION_ROOT = Path(".clicktutor_sessions")


def get_session_dir():
    if "session_dir" not in st.session_state:
        session_dir = SESSION_ROOT / uuid4().hex
        session_dir.mkdir(parents=True, exist_ok=True)
        st.session_state.session_dir = str(session_dir)

    return Path(st.session_state.session_dir)


def image_suffix(filename):
    suffix = Path(filename or "uploaded.png").suffix.lower()

    if suffix not in [".png", ".jpg", ".jpeg"]:
        return ".png"

    return suffix


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

    session_dir = get_session_dir()
    current_image = uploaded_file.getvalue()

    # ==================================
    # DETECT IMAGE CHANGES
    # ==================================

    image_changed = (
        "last_uploaded_image" not in st.session_state
        or current_image != st.session_state.last_uploaded_image
    )

    if image_changed:

        print("New image detected")

        st.session_state.last_uploaded_image = current_image

        for key in [

            "session",

            "messages",

            "highlighted_image",

            "selected_image"

        ]:

            if key in st.session_state:

                del st.session_state[key]

        upload_path = session_dir / f"uploaded{image_suffix(uploaded_file.name)}"

        with open(upload_path, "wb") as f:
            f.write(current_image)

        st.session_state.uploaded_image = str(upload_path)
        st.session_state.selected_image = str(upload_path)

    image_path = st.session_state.uploaded_image
    image = Image.open(image_path)

    # ==================================
    # DEFAULT IMAGE
    # ==================================

    if "selected_image" not in st.session_state:

        st.session_state.selected_image = image_path

    col1, col2 = st.columns([1, 2])

    # ==================================
    # IMAGE PREVIEW
    # ==================================

    with col1:

        st.image(
            image_path,
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

            selected_path = session_dir / "selected_region.png"

            cropped.save(
                selected_path
            )

            st.session_state.selected_image = str(
                selected_path
            )

            st.image(
                cropped,
                caption="Selected Region"
            )

        st.caption(
            f"Current Image: {Path(st.session_state.selected_image).name}"
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

            # Remove any highlight from a previous session
            if "highlighted_image" in st.session_state:
                del st.session_state["highlighted_image"]

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

                answer, highlighted = (
                    st.session_state.session.ask(
                        prompt
                    )
                )

                if highlighted:

                    st.session_state.highlighted_image = highlighted

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
