"""Refunds, cancellation and status queries."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

import pytest

from bluemedia import (
    CANCEL_FIELD_ORDER,
    CANCEL_PATH,
    CANCEL_RESPONSE_FIELD_ORDER,
    FAILURE,
    PENDING,
    REFUND_FIELD_ORDER,
    REFUND_PATH,
    REFUND_RESPONSE_FIELD_ORDER,
    SANDBOX_BASE_URL,
    STATUS_PATH,
    SUCCESS,
    TRANSACTION_STATUS_FIELD_ORDER,
    CancelRequest,
    CancelResponse,
    Client,
    GatewayError,
    MissingReferenceError,
    Notification,
    NotificationList,
    RefundAmountError,
    RefundRequest,
    RefundResponse,
    ResponseMismatchError,
    ResponseVerificationError,
    StatusRequest,
    new_message_id,
    string_to_sign,
)

KEY = "9dfc5eb3d1b2f4e0"
OTHER_KEY = "another-shared-key"
SERVICE_ID = "123456"
MESSAGE_ID = "M-1"


class Response:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class Transport:
    """Answers with a canned response, or with one built from what was sent."""

    def __init__(self, response: Response | Callable[[str], Response]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url, *, content, headers, timeout):
        self.calls.append({"url": url, "content": content})
        if callable(self.response):
            return self.response(content.decode("utf-8"))
        return self.response


def refund_answer(key: str = KEY, **overrides: Any) -> str:
    message = RefundResponse(
        service_id=SERVICE_ID,
        message_id=MESSAGE_ID,
        remote_id="AB-1",
        remote_out_id="AB-1-R",
        amount="10.99",
        currency="PLN",
        refund_status=SUCCESS,
    )
    if overrides:
        message = dataclasses.replace(message, **overrides)
    return message.signed(key).to_xml()


def echoing_refund(**overrides: Any) -> Callable[[str], Response]:
    """Answer every refund with the message id its request carried."""

    def respond(document: str) -> Response:
        sent_id = RefundRequest.from_xml(document).message_id
        return Response(refund_answer(message_id=sent_id, **overrides))

    return respond


def cancel_answer(key: str = KEY, **overrides: Any) -> str:
    message = CancelResponse(
        service_id=SERVICE_ID,
        message_id=MESSAGE_ID,
        remote_id="AB-1",
        order_id="5555",
        cancel_status=SUCCESS,
    )
    if overrides:
        message = dataclasses.replace(message, **overrides)
    return message.signed(key).to_xml()


def notification(**overrides: Any) -> Notification:
    message = Notification(
        service_id=SERVICE_ID,
        order_id="5555",
        remote_id="AB-1",
        amount="10.99",
        currency="PLN",
        payment_date="20260818120000",
        payment_status="SUCCESS",
    )
    return dataclasses.replace(message, **overrides) if overrides else message


def status_answer(*notifications: Notification) -> str:
    return NotificationList(
        service_id=SERVICE_ID, notifications=notifications
    ).to_xml()


def client(
    body: str | Callable[[str], Response], **kwargs: Any
) -> tuple[Client, Transport]:
    transport = Transport(body if callable(body) else Response(body))
    return Client(SERVICE_ID, KEY, http=transport, **kwargs), transport


def sent(transport: Transport, model: type) -> Any:
    return model.from_xml(transport.calls[0]["content"].decode("utf-8"))


@pytest.mark.parametrize(
    ("model", "order"),
    [
        (RefundRequest, REFUND_FIELD_ORDER),
        (RefundResponse, REFUND_RESPONSE_FIELD_ORDER),
        (CancelRequest, CANCEL_FIELD_ORDER),
        (CancelResponse, CANCEL_RESPONSE_FIELD_ORDER),
        (StatusRequest, TRANSACTION_STATUS_FIELD_ORDER),
    ],
)
def test_declared_fields_are_exactly_the_signed_fields(model, order):
    declared = [
        f.metadata["spec"] for f in dataclasses.fields(model) if f.name != "hash"
    ]
    assert [name.lower() for name in declared] == [name.lower() for name in order]


def test_a_full_refund_names_no_amount():
    request = RefundRequest(
        service_id=SERVICE_ID, message_id=MESSAGE_ID, remote_id="AB-1"
    )
    assert request.partial is False
    assert "amount" not in request.to_xml()
    assert string_to_sign(request.to_fields(), REFUND_FIELD_ORDER) == (
        "123456|M-1|AB-1"
    )


def test_a_partial_refund_signs_the_amount_where_the_order_puts_it():
    request = RefundRequest(
        service_id=SERVICE_ID,
        message_id=MESSAGE_ID,
        remote_id="AB-1",
        order_id="5555",
        amount="5.00",
        currency="PLN",
    )
    assert request.partial is True
    assert string_to_sign(request.to_fields(), REFUND_FIELD_ORDER) == (
        "123456|M-1|AB-1|5555|5.00|PLN"
    )


@pytest.mark.parametrize("amount", ["", "   ", "\n"])
def test_a_blank_amount_does_not_become_a_full_refund(amount):
    with pytest.raises(RefundAmountError) as excinfo:
        RefundRequest(
            service_id=SERVICE_ID,
            message_id=MESSAGE_ID,
            remote_id="AB-1",
            amount=amount,
        )
    assert isinstance(excinfo.value, ValueError)


def test_a_blank_amount_reaches_no_transport():
    gateway, transport = client(refund_answer())
    with pytest.raises(RefundAmountError):
        gateway.refund(remote_id="AB-1", amount="")
    assert transport.calls == []


@pytest.mark.parametrize("model", [RefundRequest, CancelRequest, StatusRequest])
def test_a_request_naming_no_payment_is_refused(model):
    with pytest.raises(MissingReferenceError):
        model(service_id=SERVICE_ID, message_id=MESSAGE_ID)


@pytest.mark.parametrize("model", [RefundRequest, CancelRequest, StatusRequest])
@pytest.mark.parametrize(
    "reference",
    [{"remote_id": ""}, {"order_id": "   "}, {"remote_id": "", "order_id": ""}],
    ids=["empty remote id", "blank order id", "both empty"],
)
def test_an_identifier_holding_nothing_names_no_payment(model, reference):
    with pytest.raises(MissingReferenceError):
        model(service_id=SERVICE_ID, message_id=MESSAGE_ID, **reference)


@pytest.mark.parametrize("reference", [{"remote_id": "AB-1"}, {"order_id": "5555"}])
def test_either_identifier_names_the_payment(reference):
    request = RefundRequest(
        service_id=SERVICE_ID, message_id=MESSAGE_ID, **reference
    )
    assert RefundRequest.from_xml(request.to_xml()) == request


def test_a_refund_is_signed_and_posted_to_its_own_endpoint():
    gateway, transport = client(refund_answer())
    gateway.refund(
        remote_id="AB-1", amount="5.00", currency="PLN", message_id=MESSAGE_ID
    )

    assert transport.calls[0]["url"] == SANDBOX_BASE_URL + REFUND_PATH
    request = sent(transport, RefundRequest)
    assert request.service_id == SERVICE_ID
    assert request.amount == "5.00"
    assert request.verify(KEY) is True


def test_the_refund_answer_comes_back_typed_and_verified():
    gateway, _ = client(refund_answer())
    answer = gateway.refund(remote_id="AB-1", message_id=MESSAGE_ID)
    assert isinstance(answer, RefundResponse)
    assert answer.remote_out_id == "AB-1-R"
    assert answer.succeeded is True


@pytest.mark.parametrize("status", [FAILURE, PENDING])
def test_only_a_finished_refund_counts_as_succeeded(status):
    gateway, _ = client(refund_answer(refund_status=status, refund_reason="too late"))
    answer = gateway.refund(remote_id="AB-1", message_id=MESSAGE_ID)
    assert answer.succeeded is False
    assert answer.refund_reason == "too late"


def test_a_refund_answer_signed_with_another_key_is_refused():
    gateway, _ = client(refund_answer(OTHER_KEY))
    with pytest.raises(ResponseVerificationError) as excinfo:
        gateway.refund(remote_id="AB-1", message_id=MESSAGE_ID)
    assert excinfo.value.response.remote_id == "AB-1"


def test_a_refund_answer_for_another_call_is_refused():
    gateway, _ = client(refund_answer(message_id="M-2"))
    with pytest.raises(ResponseMismatchError) as excinfo:
        gateway.refund(remote_id="AB-1", message_id=MESSAGE_ID)

    assert isinstance(excinfo.value, GatewayError)
    assert excinfo.value.field == "messageID"
    assert excinfo.value.expected == MESSAGE_ID
    assert excinfo.value.found == "M-2"
    assert MESSAGE_ID in str(excinfo.value)
    assert "M-2" in str(excinfo.value)
    assert excinfo.value.response.remote_out_id == "AB-1-R"


def test_a_cancellation_answer_for_another_call_is_refused():
    gateway, _ = client(cancel_answer(message_id="M-2"))
    with pytest.raises(ResponseMismatchError) as excinfo:
        gateway.cancel(order_id="5555", message_id=MESSAGE_ID)
    assert excinfo.value.expected == MESSAGE_ID
    assert excinfo.value.found == "M-2"


def test_the_match_does_not_depend_on_verification():
    gateway, _ = client(
        refund_answer(OTHER_KEY, message_id="M-2"), verify_responses=False
    )
    with pytest.raises(ResponseMismatchError):
        gateway.refund(remote_id="AB-1", message_id=MESSAGE_ID)


def test_a_verification_failure_is_reported_before_the_match():
    gateway, _ = client(refund_answer(OTHER_KEY, message_id="M-2"))
    with pytest.raises(ResponseVerificationError):
        gateway.refund(remote_id="AB-1", message_id=MESSAGE_ID)


def test_a_message_id_is_made_up_when_the_caller_brings_none():
    gateway, transport = client(echoing_refund())
    gateway.refund(remote_id="AB-1")
    gateway.refund(remote_id="AB-2")

    ids = {
        RefundRequest.from_xml(call["content"].decode("utf-8")).message_id
        for call in transport.calls
    }
    assert len(ids) == 2
    assert len(new_message_id()) == 32


def test_a_message_id_the_caller_brings_is_kept():
    gateway, transport = client(refund_answer())
    gateway.refund(remote_id="AB-1", message_id=MESSAGE_ID)
    assert sent(transport, RefundRequest).message_id == MESSAGE_ID


def test_a_request_and_loose_fields_are_not_mixed():
    gateway, _ = client(refund_answer())
    request = RefundRequest(
        service_id=SERVICE_ID, message_id=MESSAGE_ID, remote_id="AB-1"
    )
    with pytest.raises(TypeError):
        gateway.refund(request, amount="5.00")


def test_a_request_naming_no_payment_reaches_no_transport():
    gateway, transport = client(refund_answer())
    with pytest.raises(MissingReferenceError):
        gateway.refund(amount="5.00")
    assert transport.calls == []


def test_a_cancellation_is_signed_and_posted_to_its_own_endpoint():
    gateway, transport = client(cancel_answer())
    answer = gateway.cancel(order_id="5555", message_id=MESSAGE_ID)

    assert transport.calls[0]["url"] == SANDBOX_BASE_URL + CANCEL_PATH
    assert sent(transport, CancelRequest).verify(KEY) is True
    assert answer.succeeded is True


def test_a_cancellation_answer_signed_with_another_key_is_refused():
    gateway, _ = client(cancel_answer(OTHER_KEY))
    with pytest.raises(ResponseVerificationError):
        gateway.cancel(order_id="5555", message_id=MESSAGE_ID)


def test_a_status_query_is_answered_with_the_document_an_itn_arrives_in():
    gateway, transport = client(status_answer(notification().signed(KEY)))
    answer = gateway.status(order_id="5555")

    assert transport.calls[0]["url"] == SANDBOX_BASE_URL + STATUS_PATH
    request = sent(transport, StatusRequest)
    assert request.order_id == "5555"
    assert request.verify(KEY) is True
    assert isinstance(answer, NotificationList)
    assert answer[0].payment_status == "SUCCESS"


def test_a_status_answer_carrying_an_unverified_payment_is_refused():
    gateway, _ = client(
        status_answer(
            notification().signed(KEY),
            notification(order_id="5556").signed(OTHER_KEY),
        )
    )
    with pytest.raises(ResponseVerificationError) as excinfo:
        gateway.status(order_id="5555")
    assert excinfo.value.response.order_id == "5556"


def test_status_verification_can_be_turned_off():
    gateway, _ = client(
        status_answer(notification().signed(OTHER_KEY)), verify_responses=False
    )
    assert len(gateway.status(order_id="5555")) == 1
