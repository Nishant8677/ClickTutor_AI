# The vocabulary the lesson prompt asks Gemini to use. Exported so the parser
# can coerce out-of-vocabulary values instead of passing them to the renderer,
# which would silently treat anything unrecognised as a rectangle.
VALID_ATTENTIONS = frozenset({"circle", "rectangle", "arrow", "underline", "none"})
VALID_EMPHASES = frozenset({"high", "medium", "low"})

REQUIRED_STEP_KEYS = (
    "step", "title", "anchor", "attention", "emphasis", "explanation",
)


def validate_lesson_steps(steps):
    """
    Validates a list of structured lesson steps.
    Returns (is_valid, errors_list)
    """
    if not isinstance(steps, list):
        return False, ["Lesson steps must be a list."]

    errors = []
    valid_attentions = VALID_ATTENTIONS
    valid_emphases = VALID_EMPHASES

    for idx, step in enumerate(steps):
        prefix = f"Step {idx + 1}"
        if not isinstance(step, dict):
            errors.append(f"{prefix} is not a dictionary.")
            continue

        # Check required keys
        for key in REQUIRED_STEP_KEYS:
            if key not in step:
                errors.append(f"{prefix} is missing key: '{key}'.")

        # Validate attention type
        attention = step.get("attention")
        if attention and attention not in valid_attentions:
            errors.append(f"{prefix} has invalid attention: '{attention}'. Must be one of {valid_attentions}.")

        # Validate emphasis type
        emphasis = step.get("emphasis")
        if emphasis and emphasis not in valid_emphases:
            errors.append(f"{prefix} has invalid emphasis: '{emphasis}'. Must be one of {valid_emphases}.")

        # Validate anchor exists (even if NONE)
        anchor = step.get("anchor")
        if not anchor:
            errors.append(f"{prefix} has empty anchor.")

    return len(errors) == 0, errors
