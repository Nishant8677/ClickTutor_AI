from pathlib import Path
from PIL import Image, ImageDraw


def highlight_box(image_path, box, output_path=None):
    """
    Draws a red rectangle around the OCR-detected text region.

    Parameters
    ----------
    image_path : str
        Path to the source image.
    box : dict
        Bounding box dict returned by ocr_locator.find_text().
        Expected keys: left, top, width, height.
    output_path : str or Path, optional
        Destination path for the output image. Defaults to
        <stem>_highlighted.png in the same directory as image_path.

    Returns
    -------
    str
        Path to the saved output image.
    """
    image = Image.open(image_path).convert("RGB")

    if output_path is None:
        image_path = Path(image_path)
        output_path = image_path.with_name(f"{image_path.stem}_highlighted.png")

    if box is None:
        image.save(output_path)
        return output_path

    draw = ImageDraw.Draw(image)

    left = box["left"]
    top = box["top"]
    width = max(1, box["width"])
    height = max(1, box["height"])
    right = left + width
    bottom = top + height

    # Add padding around the detected region
    padding = 6
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)

    draw.rectangle((left, top, right, bottom), outline="red", width=4)

    image.save(output_path)
    return output_path
