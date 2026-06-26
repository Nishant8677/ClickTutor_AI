from PIL import Image, ImageDraw


def highlight_box(image_path, box):

    """
    Draws a red rectangle around the OCR-detected text.

    Parameters
    ----------
    image_path : str
        Path to the image.

    box : dict
        Dictionary returned by ocr_locator.py

        Example:
        {
            "left": 120,
            "top": 80,
            "width": 65,
            "height": 20
        }

    Returns
    -------
    str
        Path to highlighted image.
    """

    image = Image.open(image_path).convert("RGB")

    if box is None:
        output_path = "highlighted_image.png"
        image.save(output_path)
        print("No OCR box found; returning unmodified image.")
        return output_path

    draw = ImageDraw.Draw(image)

    if box is not None:
        print("BOX RECEIVED:", box)

        left = box["left"]
        top = box["top"]

        width = max(1, box["width"])
        height = max(1, box["height"])

        right = left + width
        bottom = top + height

        print(
            f"left={left}, top={top}, right={right}, bottom={bottom}"
        )

        # Padding around the detected text
        padding = 6

        left = max(0, left - padding)
        top = max(0, top - padding)

        right = min(image.width, right + padding)
        bottom = min(image.height, bottom + padding)

        draw.rectangle(
            (
                left,
                top,
                right,
                bottom
            ),
            outline="red",
            width=4
        )

    output_path = "highlighted_image.png"

    image.save(output_path)

    print("RETURNING:", {
        "left": left,
        "top": top,
        "width": right - left,
        "height": bottom - top
    })
    print("Original image size:", image.width, image.height)
    print("OCR box:", box)

    return output_path
