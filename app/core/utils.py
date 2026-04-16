import string
import random

def generate_unique_reference(prefix: str = "PG", length: int = 6) -> str:
    """
    Generate a clean, unambiguous alphanumeric reference code.
    Omit confusing characters like I, L, O, 0, 1 for clarity.
    Example: PG-A4K9M2
    """
    chars = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    suffix = "".join(random.choices(chars, k=length))
    return f"{prefix}-{suffix}"
