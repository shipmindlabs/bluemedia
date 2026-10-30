"""A start's answer has to name the order the request named."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from bluemedia import (
    Client,
    GatewayError,
    ResponseMismatchError,
    ResponseVerificationError,
    TransactionInit,
    TransactionRefusal,
    TransactionRefused,
)

KEY = "9dfc5eb3d1b2f4e0"
SERVICE_ID = "123456"


class Response:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class Transport:
    def __init__(self, response: Response) -> None:
        self.response = response

    def post(self, url, *, content, headers, timeout):
        return self.response


def init(**overrides: Any) -> TransactionInit:
    message = TransactionInit(
        service_id=SERVICE_ID,
        order_id="5555",
        remote_id="AB-1",
        amount="10.99",
        currency="PLN",
        gateway_id="21",
        payment_status="PENDING",
        redirect_url="https://pay.example.com/AB-1",
    )
    return dataclasses.replace(message, **overrides) if overrides else message


def refusal(**overrides: Any) -> TransactionRefusal:
    message = TransactionRefusal(
        service_id=SERVICE_ID,
        order_id="5555",
        confirmation="NOTCONFIRMED",
        reason="WRONG_HASH",
    )
    return dataclasses.replace(message, **overrides) if overrides else message


def client(document: str, **kwargs: Any) -> Client:
    return Client(SERVICE_ID, KEY, http=Transport(Response(document)), **kwargs)


def start(gateway: Client) -> TransactionInit:
    return gateway.start(order_id="5555", amount="10.99", currency="PLN")


def test_the_answer_to_this_request_comes_back():
    assert start(client(init().signed(KEY).to_xml())).remote_id == "AB-1"


def test_an_answer_for_another_order_is_refused():
    gateway = client(init(order_id="6666").signed(KEY).to_xml())
    with pytest.raises(ResponseMismatchError) as excinfo:
        start(gateway)

    assert isinstance(excinfo.value, GatewayError)
    assert excinfo.value.field == "orderID"
    assert excinfo.value.expected == "5555"
    assert excinfo.value.found == "6666"
    assert "5555" in str(excinfo.value)
    assert "6666" in str(excinfo.value)
    assert excinfo.value.response.remote_id == "AB-1"


def test_a_refusal_for_another_order_is_refused():
    gateway = client(refusal(order_id="6666").signed(KEY).to_xml())
    with pytest.raises(ResponseMismatchError) as excinfo:
        start(gateway)

    assert not isinstance(excinfo.value, TransactionRefused)
    assert excinfo.value.response.confirmation == "NOTCONFIRMED"


def test_a_refusal_for_this_order_still_refuses():
    gateway = client(refusal().signed(KEY).to_xml())
    with pytest.raises(TransactionRefused) as excinfo:
        start(gateway)
    assert excinfo.value.order_id == "5555"


def test_the_match_does_not_depend_on_verification():
    gateway = client(init(order_id="6666").to_xml(), verify_responses=False)
    with pytest.raises(ResponseMismatchError):
        start(gateway)


def test_a_verification_failure_is_reported_before_the_match():
    gateway = client(init(order_id="6666").signed("another-shared-key").to_xml())
    with pytest.raises(ResponseVerificationError):
        start(gateway)
