"""Edge cases of the operation exchange: refund, cancel and status query.

What can go wrong with these three is mostly what comes back. An answer from
another endpoint, a body that is not a document, a digest in the casing some
gateway happens to send, a status that is neither success nor failure, a
payment reported without a digest at all: each has to be a rejection with a
name rather than a value a shop would book. The request side has its own
corners -- an amount of zero is still an amount, one identifier holding
nothing is still a named payment -- and they decide what reaches the digest.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from bluemedia import (
    DEFAULT_ALGORITHM,
    DEFAULT_SEPARATOR,
    FAILURE,
    PENDING,
    REFUND_FIELD_ORDER,
    SUCCESS,
    CancelRequest,
    CancelResponse,
    Client,
    DuplicateElementError,
    GatewayStatusError,
    MalformedXMLError,
    MissingElementError,
    MissingReferenceError,
    Notification,
    NotificationList,
    RefundAmountError,
    RefundRequest,
    RefundResponse,
    ResponseVerificationError,
    StatusRequest,
    UnexpectedRootError,
    UnknownElementError,
    string_to_sign,
)

KEY = "9dfc5eb3d1b2f4e0"
SERVICE_ID = "123456"
MESSAGE_ID = "M-1"


class Response:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class Transport:
    """Answers with a canned response, or with one built from what was sent."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url, *, content, headers, timeout):
        self.calls.append({"url": url, "content": content})
        if callable(self.response):
            return self.response(content.decode("utf-8"))
        return self.response


def refund_request(**overrides: Any) -> RefundRequest:
    message = RefundRequest(
        service_id=SERVICE_ID, message_id=MESSAGE_ID, remote_id="AB-1"
    )
    return dataclasses.replace(message, **overrides) if overrides else message


def refund_response(**overrides: Any) -> RefundResponse:
    message = RefundResponse(
        service_id=SERVICE_ID,
        message_id=MESSAGE_ID,
        remote_id="AB-1",
        remote_out_id="AB-1-R",
        amount="10.99",
        currency="PLN",
        refund_status=SUCCESS,
    )
    return dataclasses.replace(message, **overrides) if overrides else message


def refund_answer(
    key: str = KEY,
    *,
    separator: str = DEFAULT_SEPARATOR,
    algorithm: str = DEFAULT_ALGORITHM,
    **overrides: Any,
) -> str:
    signed = refund_response(**overrides).signed(
        key, separator=separator, algorithm=algorithm
    )
    return signed.to_xml()


def echoing_refund():
    """Answer every refund with the message id its request carried."""

    def respond(document: str) -> Response:
        sent_id = RefundRequest.from_xml(document).message_id
        return Response(refund_answer(message_id=sent_id))

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
        payment_status=SUCCESS,
    )
    return dataclasses.replace(message, **overrides) if overrides else message


def status_answer(*notifications: Notification) -> str:
    return NotificationList(
        service_id=SERVICE_ID, notifications=notifications
    ).to_xml()


def client(body: Any, **kwargs: Any) -> tuple[Client, Transport]:
    transport = Transport(Response(body) if isinstance(body, str) else body)
    return Client(SERVICE_ID, KEY, http=transport, **kwargs), transport


def sent(transport: Transport, model: type) -> Any:
    return model.from_xml(transport.calls[0]["content"].decode("utf-8"))


def test_one_identifier_holding_nothing_still_names_the_payment():
    request = RefundRequest(
        service_id=SERVICE_ID,
        message_id=MESSAGE_ID,
        remote_id="",
        order_id="5555",
    )
    assert string_to_sign(request.to_fields(), REFUND_FIELD_ORDER) == (
        "123456|M-1|5555"
    )


def test_a_refund_of_nothing_is_still_a_refund_for_an_amount():
    request = refund_request(amount="0.00")
    assert request.partial is True
    assert string_to_sign(request.to_fields(), REFUND_FIELD_ORDER) == (
        "123456|M-1|AB-1|0.00"
    )


def test_the_refused_amount_is_reported_back():
    with pytest.raises(RefundAmountError) as excinfo:
        RefundRequest(
            service_id=SERVICE_ID,
            message_id=MESSAGE_ID,
            remote_id="AB-1",
            amount="\t",
        )
    assert excinfo.value.value == "\t"
    assert repr("\t") in str(excinfo.value)


def test_a_whole_refund_signs_its_currency_across_the_absent_amount():
    request = refund_request(currency="PLN")
    assert request.partial is False
    assert string_to_sign(request.to_fields(), REFUND_FIELD_ORDER) == (
        "123456|M-1|AB-1|PLN"
    )


def test_an_operation_digest_never_signs_itself():
    request = refund_request(amount="5.00")
    signed = request.signed(KEY)
    assert signed.sign(KEY) == request.sign(KEY)
    assert signed.verify(KEY) is True


@pytest.mark.parametrize(
    "given",
    [SUCCESS, "success", " Success\n"],
    ids=["as written", "lower case", "padded and mixed"],
)
def test_a_status_the_gateway_pads_or_cases_still_counts_as_done(given):
    refund = RefundResponse(
        service_id=SERVICE_ID, message_id=MESSAGE_ID, refund_status=given
    )
    cancel = CancelResponse(
        service_id=SERVICE_ID, message_id=MESSAGE_ID, cancel_status=given
    )
    assert refund.succeeded is True
    assert cancel.succeeded is True


@pytest.mark.parametrize(
    "given",
    [PENDING, FAILURE, "SUCCESSFUL", "OK", ""],
    ids=["pending", "failure", "a longer word", "another word", "nothing"],
)
def test_nothing_but_success_counts_as_done(given):
    refund = RefundResponse(
        service_id=SERVICE_ID, message_id=MESSAGE_ID, refund_status=given
    )
    cancel = CancelResponse(
        service_id=SERVICE_ID, message_id=MESSAGE_ID, cancel_status=given
    )
    assert refund.succeeded is False
    assert cancel.succeeded is False


def test_an_answer_carrying_only_what_it_must_round_trips():
    message = RefundResponse(
        service_id=SERVICE_ID,
        message_id=MESSAGE_ID,
        refund_status=FAILURE,
        refund_reason="TOO_LATE",
    ).signed(KEY)

    assert RefundResponse.from_xml(message.to_xml()) == message
    assert message.verify(KEY) is True
    assert message.succeeded is False


def test_an_answer_without_a_status_does_not_parse():
    with pytest.raises(MissingElementError) as excinfo:
        RefundResponse.from_xml(
            "<transactionRefundResponse>"
            "<serviceID>123456</serviceID><messageID>M-1</messageID>"
            "</transactionRefundResponse>"
        )
    assert excinfo.value.names == ("refundStatus",)


def test_an_answer_carrying_an_undeclared_element_is_refused():
    with pytest.raises(UnknownElementError) as excinfo:
        RefundResponse.from_xml(
            "<transactionRefundResponse>"
            "<serviceID>123456</serviceID><messageID>M-1</messageID>"
            "<refundStatus>SUCCESS</refundStatus>"
            "<refundAmount>5.00</refundAmount>"
            "</transactionRefundResponse>"
        )
    assert excinfo.value.name == "refundAmount"


def test_an_answer_saying_the_same_thing_twice_is_refused():
    with pytest.raises(DuplicateElementError) as excinfo:
        CancelResponse.from_xml(
            "<transactionCancelResponse>"
            "<serviceID>123456</serviceID>"
            "<messageID>M-1</messageID><messageID>M-2</messageID>"
            "<cancelStatus>SUCCESS</cancelStatus>"
            "</transactionCancelResponse>"
        )
    assert excinfo.value.name == "messageID"


@pytest.mark.parametrize("model", [RefundRequest, CancelRequest, StatusRequest])
def test_a_request_parsed_without_a_payment_is_refused(model):
    root = model.XML_ROOT
    document = (
        f"<{root}><serviceID>123456</serviceID>"
        f"<messageID>M-1</messageID></{root}>"
    )
    with pytest.raises(MissingReferenceError):
        model.from_xml(document)


@pytest.mark.parametrize("call", ["cancel", "status"])
def test_an_operation_naming_no_payment_reaches_no_transport(call):
    gateway, transport = client(refund_answer())
    with pytest.raises(MissingReferenceError):
        getattr(gateway, call)()
    assert transport.calls == []


@pytest.mark.parametrize(
    ("call", "model"), [("cancel", CancelRequest), ("status", StatusRequest)]
)
def test_an_operation_request_and_loose_fields_are_not_mixed(call, model):
    gateway, transport = client(refund_answer())
    request = model(
        service_id=SERVICE_ID, message_id=MESSAGE_ID, order_id="5555"
    )
    with pytest.raises(TypeError) as excinfo:
        getattr(gateway, call)(request, order_id="5556")

    assert model.__name__ in str(excinfo.value)
    assert transport.calls == []


def test_a_prepared_refund_is_sent_with_its_own_fields():
    gateway, transport = client(refund_answer(message_id="M-9"))
    answer = gateway.refund(
        RefundRequest(
            service_id="654321",
            message_id="M-9",
            order_id="5555",
            amount="5.00",
        )
    )

    request = sent(transport, RefundRequest)
    assert request.service_id == "654321"
    assert request.amount == "5.00"
    assert answer.message_id == "M-9"


@pytest.mark.parametrize("call", ["refund", "cancel", "status"])
def test_a_failing_status_stops_every_operation(call):
    gateway, transport = client(Response("service unavailable", status_code=503))
    with pytest.raises(GatewayStatusError) as excinfo:
        getattr(gateway, call)(remote_id="AB-1")

    assert excinfo.value.status_code == 503
    assert excinfo.value.body == "service unavailable"
    assert len(transport.calls) == 1


def test_an_answer_from_another_endpoint_is_a_parse_error():
    gateway, _ = client(cancel_answer())
    with pytest.raises(UnexpectedRootError) as excinfo:
        gateway.refund(remote_id="AB-1", message_id=MESSAGE_ID)
    assert excinfo.value.expected == "transactionRefundResponse"


def test_a_status_query_answered_with_a_refund_is_a_parse_error():
    gateway, _ = client(refund_answer())
    with pytest.raises(UnexpectedRootError):
        gateway.status(order_id="5555")


def test_a_body_that_is_not_a_document_is_a_parse_error():
    gateway, _ = client("gateway down")
    with pytest.raises(MalformedXMLError):
        gateway.cancel(order_id="5555")


def test_an_answer_whose_digest_arrives_upper_cased_still_verifies():
    signed = refund_response().signed(KEY)
    loud = dataclasses.replace(signed, hash=signed.hash.upper())
    gateway, _ = client(loud.to_xml())
    assert gateway.refund(
        remote_id="AB-1", message_id=MESSAGE_ID
    ).succeeded is True


def test_a_padded_message_id_still_matches_the_answer():
    gateway, _ = client(echoing_refund())
    answer = gateway.refund(remote_id="AB-1", message_id=f"  {MESSAGE_ID}  ")
    assert answer.message_id == MESSAGE_ID


def test_the_algorithm_reaches_an_operation_in_both_directions():
    gateway, transport = client(
        refund_answer(algorithm="sha512"), algorithm="sha512"
    )
    answer = gateway.refund(remote_id="AB-1", message_id=MESSAGE_ID)

    assert len(sent(transport, RefundRequest).hash) == 128
    assert answer.verify(KEY, algorithm="sha512") is True


def test_the_separator_reaches_an_operation_in_both_directions():
    gateway, transport = client(refund_answer(separator="&"), separator="&")
    gateway.refund(remote_id="AB-1", message_id=MESSAGE_ID)

    request = sent(transport, RefundRequest)
    assert request.verify(KEY, separator="&") is True
    assert request.verify(KEY) is False


def test_a_status_answer_reporting_nothing_comes_back_empty():
    gateway, _ = client(status_answer())
    answer = gateway.status(order_id="5555")
    assert len(answer) == 0
    assert list(answer) == []


def test_a_status_answer_carrying_an_unsigned_payment_is_refused():
    gateway, _ = client(status_answer(notification()))
    with pytest.raises(ResponseVerificationError) as excinfo:
        gateway.status(order_id="5555")
    assert excinfo.value.response.order_id == "5555"


def test_the_algorithm_reaches_the_payments_a_status_answer_reports():
    gateway, _ = client(
        status_answer(notification().signed(KEY, algorithm="sha512")),
        algorithm="sha512",
    )
    assert len(gateway.status(order_id="5555")) == 1
