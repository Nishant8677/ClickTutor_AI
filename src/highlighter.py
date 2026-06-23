from PIL import Image, ImageDraw


def highlight_region(
    image_path,
    location,
    size="medium"
):

    image = Image.open(image_path)

    width, height = image.size

    draw = ImageDraw.Draw(image)

    regions = {
        "top-left": (
            0,
            0,
            width // 3,
            height // 3
        ),

        "top-center": (
            width // 3,
            0,
            2 * width // 3,
            height // 3
        ),

        "top-right": (
            2 * width // 3,
            0,
            width,
            height // 3
        ),

        "center-left": (
            0,
            height // 3,
            width // 3,
            2 * height // 3
        ),

        "center": (
            width // 3,
            height // 3,
            2 * width // 3,
            2 * height // 3
        ),

        "center-right": (
            2 * width // 3,
            height // 3,
            width,
            2 * height // 3
        ),

        "bottom-left": (
            0,
            2 * height // 3,
            width // 3,
            height
        ),

        "bottom-center": (
            width // 3,
            2 * height // 3,
            2 * width // 3,
            height
        ),

        "bottom-right": (
            2 * width // 3,
            2 * height // 3,
            width,
            height
        )
    }

    if location in regions:

        x1, y1, x2, y2 = regions[location]

        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        if size == "small":

            box_w = (x2 - x1) // 2
            box_h = (y2 - y1) // 2

        elif size == "large":

            box_w = int((x2 - x1) * 0.9)
            box_h = int((y2 - y1) * 0.9)

        else:

            box_w = int((x2 - x1) * 0.7)
            box_h = int((y2 - y1) * 0.7)

        new_x1 = max(0, cx - box_w // 2)
        new_y1 = max(0, cy - box_h // 2)

        new_x2 = min(width, cx + box_w // 2)
        new_y2 = min(height, cy + box_h // 2)

        draw.rectangle(
            (
                new_x1,
                new_y1,
                new_x2,
                new_y2
            ),
            outline="red",
            width=6
        )

    output_path = "highlighted_image.png"

    image.save(output_path)

    return output_path
