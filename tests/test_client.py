"""Starting a transaction over an injected transport."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from bluemedia import (
    PRODUCTION_BASE_URL,
    SANDBOX_BASE_URL,
    TRANSACTION_PATH,
    Client,
    GatewayError,
    GatewayStatusError,
    ResponseVerificationError,
    TransactionInit,
    TransactionStart,
    XMLParseError,
)

KEY = "9dfc5eb3d1b2f4e0"
SERVICE_ID = "123456"


class Response:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class Transport:
    """Records what the client sends and answers with a canned response."""

    def __init__(self, response: Response) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def post(self, url, *, content, headers, timeout):
        self.calls.append(
            {"url": url, "content": content, "headers": headers, "timeout": timeout}
        )
        return self.response

    def close(self) -> None:
        self.closed = True


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


def answer(key: str = KEY, *, algorithm: str = "sha256", **overrides: Any) -> str:
    return init(**overrides).signed(key, algorithm=algorithm).to_xml()


def client(response: Response | None = None, **kwargs: Any) -> tuple[Client, Transport]:
    transport = Transport(response if response is not None else Response(answer()))
    return Client(SERVICE_ID, KEY, http=transport, **kwargs), transport


def sent_request(transport: Transport) -> TransactionStart:
    return TransactionStart.from_xml(transport.calls[0]["content"].decode("utf-8"))


def test_the_request_is_signed_and_posted_to_the_sandbox():
    gateway, transport = client()
    gateway.start(order_id="5555", amount="10.99", currency="PLN")

    call = transport.calls[0]
    assert call["url"] == SANDBOX_BASE_URL + TRANSACTION_PATH
    assert call["headers"]["Content-Type"].startswith("application/xml")
    assert sent_request(transport).verify(KEY) is True


def test_the_service_id_comes_from_the_client():
    gateway, transport = client()
    gateway.start(order_id="5555", amount="10.99")
    assert sent_request(transport).service_id == SERVICE_ID


def test_a_prepared_request_is_sent_unchanged():
    gateway, transport = client()
    request = TransactionStart(
        service_id="654321", order_id="5555", amount="10.99", title="a gift"
    )
    gateway.start(request)

    sent = sent_request(transport)
    assert sent.service_id == "654321"
    assert sent.title == "a gift"


def test_a_request_and_loose_fields_are_not_mixed():
    gateway, _ = client()
    with pytest.raises(TypeError):
        gateway.start(TransactionStart(service_id=SERVICE_ID, order_id="1", amount="1.00"), amount="2.00")


def test_prepare_signs_without_sending():
    gateway, transport = client()
    request = gateway.prepare(order_id="5555", amount="10.99")
    assert request.verify(KEY) is True
    assert transport.calls == []


def test_non_ascii_values_travel_as_utf8():
    gateway, transport = client()
    gateway.start(order_id="5555", amount="10.99", title="Zapłata za zamówienie")

    sent = sent_request(transport)
    assert sent.title == "Zapłata za zamówienie"
    assert sent.verify(KEY) is True


def test_the_answer_comes_back_typed_and_verified():
    gateway, _ = client()
    answered = gateway.start(order_id="5555", amount="10.99")
    assert isinstance(answered, TransactionInit)
    assert answered.remote_id == "AB-1"
    assert answered.redirect_url == "https://pay.example.com/AB-1"


def test_an_answer_signed_with_another_key_is_refused():
    gateway, _ = client(Response(answer("another-shared-key")))
    with pytest.raises(ResponseVerificationError) as excinfo:
        gateway.start(order_id="5555", amount="10.99")
    assert excinfo.value.response.remote_id == "AB-1"


def test_an_unsigned_answer_is_refused():
    gateway, _ = client(Response(init().to_xml()))
    with pytest.raises(ResponseVerificationError):
        gateway.start(order_id="5555", amount="10.99")


def test_verification_can_be_turned_off():
    gateway, _ = client(Response(answer("another-shared-key")), verify_responses=False)
    assert gateway.start(order_id="5555", amount="10.99").remote_id == "AB-1"


def test_a_failing_status_carries_the_body():
    gateway, _ = client(Response("service unavailable", status_code=503))
    with pytest.raises(GatewayStatusError) as excinfo:
        gateway.start(order_id="5555", amount="10.99")
    assert excinfo.value.status_code == 503
    assert excinfo.value.body == "service unavailable"
    assert isinstance(excinfo.value, GatewayError)


def test_a_body_that_is_not_a_document_raises_a_parse_error():
    gateway, _ = client(Response("<html>maintenance</html>"))
    with pytest.raises(XMLParseError):
        gateway.start(order_id="5555", amount="10.99")


def test_the_timeout_reaches_the_transport():
    gateway, transport = client(timeout=2.5)
    gateway.start(order_id="5555", amount="10.99")
    assert transport.calls[0]["timeout"] == 2.5


@pytest.mark.parametrize("base_url", [PRODUCTION_BASE_URL, PRODUCTION_BASE_URL + "/"])
def test_the_base_url_is_configurable_and_joined_once(base_url):
    gateway, transport = client(base_url=base_url)
    gateway.start(order_id="5555", amount="10.99")
    assert transport.calls[0]["url"] == PRODUCTION_BASE_URL + TRANSACTION_PATH


def test_the_algorithm_reaches_both_directions():
    gateway, transport = client(
        Response(answer(algorithm="sha512")), algorithm="sha512"
    )
    answered = gateway.start(order_id="5555", amount="10.99")
    assert answered.verify(KEY, algorithm="sha512") is True
    assert len(sent_request(transport).hash) == 128


def test_an_injected_transport_is_not_closed_by_the_client():
    transport = Transport(Response(answer()))
    with Client(SERVICE_ID, KEY, http=transport) as gateway:
        gateway.start(order_id="5555", amount="10.99")
    assert transport.closed is False


@pytest.mark.parametrize(("service_id", "key"), [("", KEY), (SERVICE_ID, "")])
def test_a_client_without_credentials_is_refused(service_id, key):
    with pytest.raises(ValueError):
        Client(service_id, key)
