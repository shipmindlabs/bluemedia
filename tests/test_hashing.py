"""Hashing the signing string with the shared key, and verifying digests."""

from __future__ import annotations

import hashlib

import pytest

from bluemedia import (
    ITN_FIELD_ORDER,
    TRANSACTION_FIELD_ORDER,
    hash_values,
    sign,
    verify,
)

KEY = "9dfc5eb3d1b2f4e0"

TRANSACTION = {
    "ServiceID": "123456",
    "OrderID": "5555",
    "Amount": "10.99",
    "GatewayID": "21",
    "Currency": "PLN",
}
TRANSACTION_PAYLOAD = f"123456|5555|10.99|21|PLN|{KEY}"

ITN = {
    "serviceID": "123456",
    "orderID": "5555",
    "remoteID": "AB-1",
    "amount": "10.99",
    "currency": "PLN",
    "paymentDate": "20260818120000",
    "paymentStatus": "SUCCESS",
}
ITN_PAYLOAD = f"123456|5555|AB-1|10.99|PLN|20260818120000|SUCCESS|{KEY}"

# The same names, deliberately out of the order the specification declares.
SHUFFLED_ORDER = ("Currency", "GatewayID", "Amount", "OrderID", "ServiceID")


def digest_of(payload: str, algorithm: str = "sha256") -> str:
    return hashlib.new(algorithm, payload.encode("utf-8")).hexdigest()


def signed(fields: dict[str, str], order: tuple[str, ...], key: str = KEY) -> dict[str, str]:
    return {**fields, "hash": sign(fields, order, key)}


@pytest.mark.parametrize(
    ("fields", "order", "payload"),
    [
        (TRANSACTION, TRANSACTION_FIELD_ORDER, TRANSACTION_PAYLOAD),
        (ITN, ITN_FIELD_ORDER, ITN_PAYLOAD),
        ({"Amount": "1.00", "ServiceID": "7"}, TRANSACTION_FIELD_ORDER, f"7|1.00|{KEY}"),
        (
            {"ServiceID": "7", "OrderID": "1", "Description": "", "Currency": None},
            TRANSACTION_FIELD_ORDER,
            f"7|1|{KEY}",
        ),
    ],
)
def test_digest_is_the_values_then_the_key(fields, order, payload):
    assert sign(fields, order, KEY) == digest_of(payload)


def test_hash_values_appends_the_key_as_the_last_part():
    assert hash_values(["a", "b"], KEY) == digest_of(f"a|b|{KEY}")


def test_hash_values_honours_a_custom_separator():
    assert hash_values(["a", "b"], KEY, separator=";") == digest_of(f"a;b;{KEY}")


@pytest.mark.parametrize("key", ["", None])
def test_an_empty_key_is_refused(key):
    with pytest.raises(ValueError):
        hash_values(["a"], key)


def test_non_ascii_values_are_hashed_as_utf8():
    fields = {**TRANSACTION, "Description": "Zapłata za zamówienie"}
    assert sign(fields, TRANSACTION_FIELD_ORDER, KEY) == digest_of(
        f"123456|5555|10.99|Zapłata za zamówienie|21|PLN|{KEY}"
    )


def test_the_hash_field_is_not_part_of_its_own_digest():
    message = signed(TRANSACTION, TRANSACTION_FIELD_ORDER)
    assert sign(message, TRANSACTION_FIELD_ORDER, KEY) == message["hash"]


def test_a_correct_message_verifies():
    assert verify(signed(ITN, ITN_FIELD_ORDER), ITN_FIELD_ORDER, KEY) is True


def test_wrong_key_produces_a_different_digest():
    assert sign(TRANSACTION, TRANSACTION_FIELD_ORDER, KEY) != sign(
        TRANSACTION, TRANSACTION_FIELD_ORDER, KEY + "0"
    )


def test_wrong_key_fails_verification():
    message = signed(ITN, ITN_FIELD_ORDER)
    assert verify(message, ITN_FIELD_ORDER, "another-shared-key") is False


def test_wrong_order_produces_a_different_digest():
    assert sign(TRANSACTION, SHUFFLED_ORDER, KEY) == digest_of(
        f"PLN|21|10.99|5555|123456|{KEY}"
    )
    assert sign(TRANSACTION, SHUFFLED_ORDER, KEY) != sign(
        TRANSACTION, TRANSACTION_FIELD_ORDER, KEY
    )


def test_wrong_order_fails_verification():
    message = signed(TRANSACTION, TRANSACTION_FIELD_ORDER)
    assert verify(message, SHUFFLED_ORDER, KEY) is False


def test_a_truncated_message_fails_verification():
    message = signed(TRANSACTION, TRANSACTION_FIELD_ORDER)
    truncated = {k: v for k, v in message.items() if k != "Currency"}
    assert verify(truncated, TRANSACTION_FIELD_ORDER, KEY) is False


def test_a_tampered_value_fails_verification():
    message = signed(TRANSACTION, TRANSACTION_FIELD_ORDER)
    assert verify({**message, "Amount": "1.99"}, TRANSACTION_FIELD_ORDER, KEY) is False


@pytest.mark.parametrize("cut", [slice(0, -1), slice(0, 32), slice(0, 0)])
def test_a_truncated_digest_fails_verification(cut):
    full = sign(TRANSACTION, TRANSACTION_FIELD_ORDER, KEY)
    assert verify(TRANSACTION, TRANSACTION_FIELD_ORDER, KEY, digest=full[cut]) is False


@pytest.mark.parametrize("decoration", ["  {}", "{}\n", "{}".upper, " {} "])
def test_verification_tolerates_case_and_surrounding_whitespace(decoration):
    full = sign(ITN, ITN_FIELD_ORDER, KEY)
    candidate = full.upper() if callable(decoration) else decoration.format(full)
    assert verify(ITN, ITN_FIELD_ORDER, KEY, digest=candidate) is True


@pytest.mark.parametrize("candidate", ["not-a-digest", "żółć", "0" * 64])
def test_a_bogus_digest_is_rejected(candidate):
    assert verify(ITN, ITN_FIELD_ORDER, KEY, digest=candidate) is False


@pytest.mark.parametrize("message", [ITN, {**ITN, "hash": None}])
def test_a_message_without_a_digest_is_unverified(message):
    assert verify(message, ITN_FIELD_ORDER, KEY) is False


def test_an_explicit_digest_wins_over_the_hash_field():
    message = signed(ITN, ITN_FIELD_ORDER)
    assert verify(message, ITN_FIELD_ORDER, KEY, digest="deadbeef") is False


@pytest.mark.parametrize(
    ("algorithm", "length"), [("sha256", 64), ("sha512", 128), ("md5", 32)]
)
def test_the_algorithm_is_configurable(algorithm, length):
    computed = sign(TRANSACTION, TRANSACTION_FIELD_ORDER, KEY, algorithm=algorithm)
    assert len(computed) == length
    assert computed == digest_of(TRANSACTION_PAYLOAD, algorithm)
    assert verify(
        TRANSACTION,
        TRANSACTION_FIELD_ORDER,
        KEY,
        digest=computed,
        algorithm=algorithm,
    )


def test_a_digest_from_another_algorithm_fails_verification():
    computed = sign(TRANSACTION, TRANSACTION_FIELD_ORDER, KEY, algorithm="sha512")
    assert verify(TRANSACTION, TRANSACTION_FIELD_ORDER, KEY, digest=computed) is False


def test_a_custom_separator_must_match_on_both_sides():
    computed = sign(ITN, ITN_FIELD_ORDER, KEY, separator=";")
    assert verify(ITN, ITN_FIELD_ORDER, KEY, digest=computed, separator=";") is True
    assert verify(ITN, ITN_FIELD_ORDER, KEY, digest=computed) is False
