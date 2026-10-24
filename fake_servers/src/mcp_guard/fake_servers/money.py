"""Money as the servers show it. Amounts are stored in paise (1 rupee = 100 paise)."""


def format_rupees(paise: int) -> str:
    """12345678 -> "₹1,23,456.78", grouped the Indian way (lakhs and crores)."""
    rupees, rest = divmod(paise, 100)
    digits = str(rupees)
    head, tail = digits[:-3], digits[-3:]
    groups = [head[max(0, i - 2) : i] for i in range(len(head), 0, -2)][::-1]
    return "₹" + ",".join([*groups, tail]) + f".{rest:02d}"
