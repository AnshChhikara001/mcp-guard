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
    # Messy cases written separately from the rules, the way Customers type and tools return.
    Example("my number is 98201 45763, please call me back", (("IN_PHONE", "98201 45763"),)),
    Example("You can reach me at +91 98450 12376 after 5 pm", (("IN_PHONE", "+91 98450 12376"),)),
    Example(
        "My mobile is 9876543210 and my refund is still pending", (("IN_PHONE", "9876543210"),)
    ),
    Example("Please verify my PAN AFZPK7190K", (("IN_PAN", "AFZPK7190K"),)),
    Example(
        "Call me on 98201-45763 and check my PAN AFZPK7190K",
        (("IN_PHONE", "98201-45763"), ("IN_PAN", "AFZPK7190K")),
    ),
    Example(
        "I changed my phone number to 9810012345. Please update my account.",
        (("IN_PHONE", "9810012345"),),
    ),
    Example("Customer contact: (98765) 43210", (("IN_PHONE", "(98765) 43210"),)),
    Example('{"phone":"+91 9845012376"}', (("IN_PHONE", "+91 9845012376"),)),
    Example(
        '{"customer":{"mobile":"9876543210","pan":"AFZPK7190K"}}',
        (("IN_PHONE", "9876543210"), ("IN_PAN", "AFZPK7190K")),
    ),
    Example(
        '{"message":"Please call me at 9810012345","ticket_id":"TKT-20261017-042"}',
        (("IN_PHONE", "9810012345"),),
    ),
    Example(
        '{"contact":"+91-98201-45763","refund_amount":2500}', (("IN_PHONE", "+91-98201-45763"),)
    ),
    Example(
        '{"customer_note":"My PAN is ABCDE1234F; refund not received"}',
        (("IN_PAN", "ABCDE1234F"),),
    ),
    Example(
        "Please call 98201 45763 about order ORD-123456 for a refund of Rs. 2500",
        (("IN_PHONE", "98201 45763"),),
    ),
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
    # Messy cases written separately from the rules.
    Example("My Aadhaar number is 2345 6789 0123", ()),  # fails the Verhoeff check
    Example("Order ID ORD-9820145763 is still pending", ()),  # digits glued to an ID prefix
    Example("Ticket #9876543210 was closed yesterday", ()),
    Example("The refund amount is Rs. 98,201.45", ()),
    Example("Transaction of INR 9,876,543.21 failed", ()),
    Example("The order was placed on 17/10/2026", ()),
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


def test_bare_ten_digits_starting_6_to_9_count_as_a_phone_even_when_they_are_not(
    detector: PiiDetector,
) -> None:
    # A known false alarm we accept: nothing here says it isn't a phone. Missing a Customer's
    # phone would leak it to the model, while a wrongly made Pseudonym still works, since the
    # Gateway restores the real value when the Agent passes it into a Tool call.
    text = "Invoice number 9820145763 needs correction"

    assert found_in(detector, text) == (("IN_PHONE", "9820145763"),)


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
