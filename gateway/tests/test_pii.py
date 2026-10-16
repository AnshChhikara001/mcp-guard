from collections import Counter
from dataclasses import dataclass

import pytest

from mcp_guard.gateway.pii import ENTITY_TYPES, PiiDetector


@dataclass(frozen=True)
class Example:
    text: str
    expected: tuple[tuple[str, str], ...]  # (entity type, matched text)


# Example texts for each Indian identifier. The values are made up, and checksums are valid
# where the identifier has one (Aadhaar, GSTIN).
EXAMPLES: tuple[Example, ...] = (
    Example("My Aadhaar is 7385 9204 6136, please update it.", (("IN_AADHAAR", "7385 9204 6136"),)),
    Example("Aadhaar number 772183824620 is linked.", (("IN_AADHAAR", "772183824620"),)),
    Example("PAN: AFZPK7190K", (("IN_PAN", "AFZPK7190K"),)),
    Example("Our GSTIN is 29AAGCR4375J1ZU for invoices.", (("IN_GSTIN", "29AAGCR4375J1ZU"),)),
    Example("Passport number J8369854, expiring 2031.", (("IN_PASSPORT", "J8369854"),)),
    Example("Voter ID ABE1234566 was submitted for KYC.", (("IN_VOTER", "ABE1234566"),)),
    Example("The car MH12AB1234 was parked outside.", (("IN_VEHICLE_REGISTRATION", "MH12AB1234"),)),
    Example("Send the refund to priya.k92@okaxis.", (("IN_UPI_ID", "priya.k92@okaxis"),)),
    Example("UPI: rahul_sharma-7@ybl", (("IN_UPI_ID", "rahul_sharma-7@ybl"),)),
    Example("Use IFSC HDFC0001234 for NEFT.", (("IN_IFSC", "HDFC0001234"),)),
    Example(
        "Reach karan.shah31@fakebank or +91 9448123456",  # the seeded bank's formats
        (("IN_UPI_ID", "karan.shah31@fakebank"), ("IN_PHONE", "+91 9448123456")),
    ),
    Example(
        "Account 50100912345678, IFSC FAKE0000214",
        (
            ("IN_BANK_ACCOUNT", "50100912345678"),
            ("IN_IFSC", "FAKE0000214"),
        ),
    ),
    Example("Branch code SBIN0005943, Andheri West", (("IN_IFSC", "SBIN0005943"),)),
    Example("Call me on +91 98201 45763 after 6.", (("IN_PHONE", "+91 98201 45763"),)),
    Example("My mobile is 9123456780", (("IN_PHONE", "9123456780"),)),
    Example("WhatsApp: +91-7012345678", (("IN_PHONE", "+91-7012345678"),)),
    Example("Account number 50100234567812", (("IN_BANK_ACCOUNT", "50100234567812"),)),
    Example("Credit a/c no. 123456789012 at HDFC", (("IN_BANK_ACCOUNT", "123456789012"),)),
    # Ten digits starting with 9 could be a phone; the word "acct" says it's an account.
    Example("Transfer to acct 9876543210 today", (("IN_BANK_ACCOUNT", "9876543210"),)),
)

# Texts that look like an identifier but aren't one, so nothing should be found.
LOOK_ALIKES: tuple[Example, ...] = (
    Example("Aadhaar 7385 9204 6137 (last digit wrong)", ()),  # fails the Verhoeff check
    Example("Aadhaar number 123456789012", ()),  # fails the Verhoeff check
    Example("Reference code XK7Q2M9P4LZ", ()),  # 11 characters, but no zero after 4 letters
    Example("Use IFSC HDFC1001234 for NEFT.", ()),  # fifth character must be a zero
    Example("Order ORDR0012345 was placed", ()),  # shaped like an IFSC, but nothing says it is
    Example("GSTIN 99AAGCR4375J1ZU", ()),  # there is no state 99
    Example("Email ananya.iyer@example.com", ()),  # an email address, not a UPI ID
    Example("Call 12345 67890", ()),  # Indian mobiles start with 6-9
    Example("Order 4021886519 shipped on 12/03/2026", ()),  # digits, but no account context
    Example("Ticket TK001 for customer C001, transaction T00012, account A001", ()),
    Example("Refund of Rs 25000 approved", ()),
)


@pytest.fixture(scope="module")
def detector() -> PiiDetector:
    # Loading the spaCy model takes a second or two, so share one detector per module.
    return PiiDetector()


def found_in(detector: PiiDetector, text: str) -> tuple[tuple[str, str], ...]:
    return tuple((m.entity_type, m.text) for m in detector.find(text))


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e.text)
def test_finds_each_identifier_with_the_right_entity_type(
    detector: PiiDetector, example: Example
) -> None:
    assert found_in(detector, example.text) == example.expected


@pytest.mark.parametrize("example", LOOK_ALIKES, ids=lambda e: e.text)
def test_does_not_find_look_alikes(detector: PiiDetector, example: Example) -> None:
    assert found_in(detector, example.text) == ()


def test_every_identifier_has_an_example() -> None:
    covered = {entity_type for example in EXAMPLES for entity_type, _ in example.expected}

    assert covered == set(ENTITY_TYPES)


def test_prints_precision_and_recall_per_identifier(
    detector: PiiDetector, capsys: pytest.CaptureFixture[str]
) -> None:
    # Precision: of what was found, the share that was right. Recall: of what should have been
    # found, the share that was. Counted over every example and look-alike text above.
    true_pos: Counter[str] = Counter()
    false_pos: Counter[str] = Counter()
    false_neg: Counter[str] = Counter()
    for example in EXAMPLES + LOOK_ALIKES:
        expected, found = Counter(example.expected), Counter(found_in(detector, example.text))
        for counts, matches in (
            (true_pos, expected & found),
            (false_pos, found - expected),
            (false_neg, expected - found),
        ):
            for (entity_type, _), n in matches.items():
                counts[entity_type] += n

    def ratio(hits: int, total: int) -> str:
        return f"{hits / total:.2f}" if total else "-"

    lines = [f"{'entity type':<26}{'precision':>10}{'recall':>8}{'examples':>10}"]
    for entity_type in ENTITY_TYPES:
        tp, fp, fn = true_pos[entity_type], false_pos[entity_type], false_neg[entity_type]
        lines.append(
            f"{entity_type:<26}{ratio(tp, tp + fp):>10}{ratio(tp, tp + fn):>8}{tp + fn:>10}"
        )
    with capsys.disabled():
        print("\n\nPII detection on the example texts\n" + "\n".join(lines))
