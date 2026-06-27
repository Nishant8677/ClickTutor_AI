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

    if "session" in st.session_state and hasattr(st.session_state.session, "screenshot_type"):
        st.info(
            f"📸 Content: **{st.session_state.session.screenshot_type.upper()}**"
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

            "lesson_steps",

            "lesson_index",

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

            # Remove previous guided lesson state
            for key in ["highlighted_image", "lesson_steps", "lesson_index"]:
                if key in st.session_state:
                    del st.session_state[key]

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

        if "lesson_steps" in st.session_state and st.session_state.lesson_steps:

            steps = st.session_state.lesson_steps
            index = st.session_state.get("lesson_index", 0)
            index = max(0, min(index, len(steps) - 1))
            st.session_state.lesson_index = index
            step = steps[index]

            st.subheader("🎯 Guided Lesson")

            st.image(
                step.get("highlighted_image")
                or st.session_state.session.image_path,
                width="stretch"
            )

            with st.container(border=True):
                st.caption(
                    f"Step {index + 1} of {len(steps)}"
                )

                st.markdown(f"### {step.get('title', f'Step {index + 1}')}")

                anchor = step.get("anchor") or step.get("visible_text")
                if anchor and anchor.upper() != "NONE":
                    st.markdown(
                        f"🎯 **Anchor Focus:** `{anchor}`"
                    )

                attention = step.get("attention", "none")
                emphasis = step.get("emphasis", "low")

                attention_emoji = {
                    "circle": "🔴 Circle",
                    "rectangle": "🟥 Rectangle",
                    "arrow": "➡️ Arrow",
                    "underline": "➖ Underline",
                    "none": "⚪ None"
                }.get(attention, f"👁️ {attention.capitalize()}")

                emphasis_emoji = {
                    "high": "🔴 High Priority",
                    "medium": "🟡 Medium Priority",
                    "low": "🟢 Low Priority"
                }.get(emphasis, f"⚡ {emphasis.capitalize()}")

                st.markdown(
                    f"👁️ **Attention Shape:** `{attention_emoji}` | ⚡ **Emphasis:** `{emphasis_emoji}`"
                )

                st.divider()

                st.markdown(
                    step.get("explanation", "")
                )

                prev_col, count_col, next_col = st.columns([1, 1, 1])

                with prev_col:
                    if st.button(
                        "◀ Previous",
                        disabled=index == 0
                    ):
                        st.session_state.lesson_index = index - 1
                        st.rerun()

                with count_col:
                    st.markdown(
                        f"<div style='text-align:center'>{index + 1} / {len(steps)}</div>",
                        unsafe_allow_html=True
                    )

                with next_col:
                    if st.button(
                        "Next ▶",
                        disabled=index == len(steps) - 1
                    ):
                        st.session_state.lesson_index = index + 1
                        st.rerun()

        elif "highlighted_image" in st.session_state:

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

                answer, highlighted, lesson_steps = (
                    st.session_state.session.ask(
                        prompt
                    )
                )

                if lesson_steps:
                    st.session_state.lesson_steps = lesson_steps
                    st.session_state.lesson_index = 0

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