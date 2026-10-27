"""A refused start: a verified typed refusal, raised with its reason."""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any

import pytest

from bluemedia import (
    TRANSACTION_INIT_FIELD_ORDER,
    TRANSACTION_REFUSAL_FIELD_ORDER,
    Client,
    GatewayError,
    MissingElementError,
    ResponseVerificationError,
    TransactionInit,
    TransactionRefusal,
    TransactionRefused,
    UnknownElementError,
    UnknownFieldError,
    string_to_sign,
)

KEY = "9dfc5eb3d1b2f4e0"
SERVICE_ID = "123456"
REASON = "transaction limit exceeded"

REFUSAL = """<?xml version="1.0" encoding="UTF-8"?>
<transaction>
  <serviceID>123456</serviceID>
  <orderID>5555</orderID>
  <confirmation>NOTCONFIRMED</confirmation>
  <reason>transaction limit exceeded</reason>
  <hash>{digest}</hash>
</transaction>
"""


class Response:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class Transport:
    """Records what the client sends and answers with a canned response."""

    def __init__(self, response: Response) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url, *, content, headers, timeout):
        self.calls.append({"url": url, "content": content})
        return self.response


def refusal(**overrides: Any) -> TransactionRefusal:
    message = TransactionRefusal(
        service_id=SERVICE_ID,
        order_id="5555",
        confirmation="NOTCONFIRMED",
        reason=REASON,
    )
    return dataclasses.replace(message, **overrides) if overrides else message


def answer(**overrides: Any) -> TransactionInit:
    message = TransactionInit(
        service_id=SERVICE_ID,
        order_id="5555",
        remote_id="AB-1",
        amount="10.99",
        currency="PLN",
        payment_status="PENDING",
        redirect_url="https://pay.example.com/AB-1",
    )
    return dataclasses.replace(message, **overrides) if overrides else message


def client(document: str, **options: Any) -> tuple[Client, Transport]:
    transport = Transport(Response(document))
    return Client(SERVICE_ID, KEY, http=transport, **options), transport


def start(gateway: Client) -> TransactionInit:
    return gateway.start(order_id="5555", amount="10.99", currency="PLN")


def digest_of(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_declared_fields_are_exactly_the_signed_refusal_fields():
    declared = [
        f.metadata["spec"]
        for f in dataclasses.fields(TransactionRefusal)
        if f.metadata["spec"] != "hash"
    ]
    assert declared == list(TRANSACTION_REFUSAL_FIELD_ORDER)


def test_the_refusal_signs_in_its_own_order():
    assert string_to_sign(
        refusal().to_fields(), TRANSACTION_REFUSAL_FIELD_ORDER
    ) == f"123456|5555|NOTCONFIRMED|{REASON}"
    assert refusal().sign(KEY) == digest_of(
        f"123456|5555|NOTCONFIRMED|{REASON}|{KEY}"
    )


def test_the_refusal_fields_are_unknown_to_the_answers_order():
    with pytest.raises(UnknownFieldError) as excinfo:
        string_to_sign(refusal().to_fields(), TRANSACTION_INIT_FIELD_ORDER)
    assert excinfo.value.names == ("confirmation", "reason")


def test_a_refusal_parses_into_typed_attributes_and_verifies():
    parsed = TransactionRefusal.from_xml(REFUSAL.format(digest=refusal().sign(KEY)))
    assert parsed.service_id == SERVICE_ID
    assert parsed.order_id == "5555"
    assert parsed.confirmation == "NOTCONFIRMED"
    assert parsed.reason == REASON
    assert parsed.verify(KEY) is True


def test_a_refusal_round_trips_through_xml():
    signed = refusal().signed(KEY)
    assert TransactionRefusal.from_xml(signed.to_xml()) == signed


def test_a_refusal_missing_its_order_is_a_parse_error():
    payload = (
        "<transaction><serviceID>123456</serviceID>"
        "<confirmation>NOTCONFIRMED</confirmation></transaction>"
    )
    with pytest.raises(MissingElementError) as excinfo:
        TransactionRefusal.from_xml(payload)
    assert excinfo.value.names == ("orderID",)


def test_the_answer_model_cannot_read_a_refusal():
    with pytest.raises(UnknownElementError) as excinfo:
        TransactionInit.from_xml(REFUSAL.format(digest="deadbeef"))
    assert excinfo.value.name == "confirmation"


def test_the_marker_element_tells_the_two_documents_apart():
    assert TransactionRefusal.refuses(REFUSAL.format(digest="deadbeef")) is True
    assert TransactionRefusal.refuses(answer().to_xml()) is False
    assert TransactionRefusal.refuses("<transactionList/>") is False


def test_a_refused_start_raises_with_the_verified_reason():
    gateway, _ = client(refusal().signed(KEY).to_xml())
    with pytest.raises(TransactionRefused) as excinfo:
        start(gateway)

    refused = excinfo.value
    assert refused.order_id == "5555"
    assert refused.reason == REASON
    assert refused.refusal == refusal().signed(KEY)
    assert REASON in str(refused)


def test_a_refusal_is_one_kind_of_gateway_error():
    assert issubclass(TransactionRefused, GatewayError)


def test_a_refusal_naming_no_reason_names_its_confirmation():
    gateway, _ = client(refusal(reason=None).signed(KEY).to_xml())
    with pytest.raises(TransactionRefused) as excinfo:
        start(gateway)
    assert excinfo.value.reason is None
    assert "NOTCONFIRMED" in str(excinfo.value)


def test_a_forged_refusal_stays_a_verification_error():
    gateway, _ = client(refusal().signed("another-shared-key").to_xml())
    with pytest.raises(ResponseVerificationError) as excinfo:
        start(gateway)
    assert isinstance(excinfo.value.response, TransactionRefusal)


def test_an_unsigned_refusal_stays_a_verification_error():
    gateway, _ = client(refusal().to_xml())
    with pytest.raises(ResponseVerificationError):
        start(gateway)


def test_a_client_that_does_not_verify_still_reports_the_refusal():
    gateway, _ = client(refusal().to_xml(), verify_responses=False)
    with pytest.raises(TransactionRefused) as excinfo:
        start(gateway)
    assert excinfo.value.reason == REASON


def test_a_refused_blik_payment_raises_the_same_way():
    gateway, _ = client(refusal().signed(KEY).to_xml())
    with pytest.raises(TransactionRefused):
        gateway.blik("777 123", order_id="5555", amount="10.99", currency="PLN")


def test_an_answer_is_still_read_as_a_payment():
    gateway, _ = client(answer().signed(KEY).to_xml())
    started = start(gateway)
    assert started.remote_id == "AB-1"
    assert started.redirect_url == "https://pay.example.com/AB-1"
