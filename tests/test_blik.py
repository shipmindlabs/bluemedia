"""BLIK: the authorization code, the gateway it runs on, and its answer."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from bluemedia import (
    BLIK_GATEWAY_ID,
    TRANSACTION_FIELD_ORDER,
    AuthorizationCodeError,
    Client,
    TransactionInit,
    TransactionStart,
    authorization_code,
    blik_transaction,
    string_to_sign,
)

KEY = "9dfc5eb3d1b2f4e0"
SERVICE_ID = "123456"
CODE = "777123"


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


def init(**overrides: Any) -> TransactionInit:
    """The answer to a code payment: a status, and nowhere to redirect to."""
    message = TransactionInit(
        service_id=SERVICE_ID,
        order_id="5555",
        remote_id="AB-1",
        amount="10.99",
        currency="PLN",
        gateway_id=BLIK_GATEWAY_ID,
        payment_status="PENDING",
    )
    return dataclasses.replace(message, **overrides) if overrides else message


def client(response: Response | None = None) -> tuple[Client, Transport]:
    transport = Transport(
        response if response is not None else Response(init().signed(KEY).to_xml())
    )
    return Client(SERVICE_ID, KEY, http=transport), transport


def sent_request(transport: Transport) -> TransactionStart:
    return TransactionStart.from_xml(transport.calls[0]["content"].decode("utf-8"))


def request(**fields: Any) -> TransactionStart:
    return blik_transaction(
        service_id=SERVICE_ID,
        order_id="5555",
        amount="10.99",
        currency="PLN",
        **fields,
    )


def test_blik_has_its_own_gateway_id():
    assert BLIK_GATEWAY_ID == "509"
    assert request().gateway_id == BLIK_GATEWAY_ID


def test_the_gateway_id_can_be_overridden():
    assert request(gateway_id="600").gateway_id == "600"


@pytest.mark.parametrize(
    "given", ["777123", " 777123 ", "777 123", "777-123", "77 71 23", "777123\n"]
)
def test_the_grouping_a_payer_copies_drops_out(given):
    assert authorization_code(given) == CODE


@pytest.mark.parametrize(
    "given",
    ["77712", "7771234", "77712a", "", "   ", "٧٧٧١٢٣", None, 777123],
    ids=[
        "too short",
        "too long",
        "not all digits",
        "empty",
        "blank",
        "non-ASCII digits",
        "none",
        "a number",
    ],
)
def test_anything_that_is_not_the_six_digits_is_refused(given):
    with pytest.raises(AuthorizationCodeError) as excinfo:
        authorization_code(given)
    assert isinstance(excinfo.value, ValueError)


def test_the_code_reaches_the_request_and_the_document():
    message = request(code="777 123")
    assert message.authorization_code == CODE
    assert f"<authorizationCode>{CODE}</authorizationCode>" in message.to_xml()


def test_the_code_signs_where_the_declared_order_puts_it():
    assert string_to_sign(request(code=CODE).to_fields(), TRANSACTION_FIELD_ORDER) == (
        f"123456|5555|10.99|509|PLN|{CODE}"
    )


def test_without_a_code_only_the_gateway_tells_it_from_a_card_payment():
    message = request()
    assert message.authorization_code is None
    assert "authorizationCode" not in message.to_xml()
    assert message == TransactionStart(
        service_id=SERVICE_ID,
        order_id="5555",
        amount="10.99",
        currency="PLN",
        gateway_id=BLIK_GATEWAY_ID,
    )


def test_a_bad_code_is_refused_before_the_request_exists():
    with pytest.raises(AuthorizationCodeError):
        request(code="12345")


def test_the_client_sends_the_code_on_the_blik_gateway():
    gateway, transport = client()
    gateway.blik("777 123", order_id="5555", amount="10.99", currency="PLN")

    sent = sent_request(transport)
    assert sent.service_id == SERVICE_ID
    assert sent.gateway_id == BLIK_GATEWAY_ID
    assert sent.authorization_code == CODE
    assert sent.verify(KEY) is True


def test_the_code_flow_answers_with_a_status_and_no_redirection():
    gateway, _ = client()
    answer = gateway.blik(CODE, order_id="5555", amount="10.99", currency="PLN")
    assert answer.payment_status == "PENDING"
    assert answer.redirect_url is None


def test_without_a_code_the_answer_redirects_like_a_card_payment():
    url = "https://pay.example.com/AB-1"
    gateway, transport = client(Response(init(redirect_url=url).signed(KEY).to_xml()))
    answer = gateway.blik(order_id="5555", amount="10.99", currency="PLN")

    assert sent_request(transport).authorization_code is None
    assert answer.redirect_url == url


def test_a_bad_code_reaches_no_transport():
    gateway, transport = client()
    with pytest.raises(AuthorizationCodeError):
        gateway.blik("12345", order_id="5555", amount="10.99")
    assert transport.calls == []
