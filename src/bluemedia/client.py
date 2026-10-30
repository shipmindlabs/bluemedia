"""Talking to the gateway: start a payment, then act on one that exists.

The transport is injected. This client calls whatever object exposes httpx's
``post(url, content=..., headers=..., timeout=...)``, which keeps the network
out of the tests and lets a caller bring its own session, retries, proxy or
instrumentation. When nothing is given, httpx is imported lazily, so the
package stays dependency-free for anyone who only signs and reads documents.

The sandbox is the default base URL: a client left unconfigured must not move
real money.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, TypeVar

from bluemedia.blik import blik_transaction
from bluemedia.itn import NotificationList
from bluemedia.models import (
    TransactionInit,
    TransactionRefusal,
    TransactionStart,
    _Document,
    _parse,
)
from bluemedia.operations import (
    CancelRequest,
    CancelResponse,
    RefundRequest,
    RefundResponse,
    StatusRequest,
    new_message_id,
)
from bluemedia.signing import DEFAULT_ALGORITHM, DEFAULT_SEPARATOR

__all__ = [
    "CANCEL_PATH",
    "DEFAULT_TIMEOUT",
    "PRODUCTION_BASE_URL",
    "REFUND_PATH",
    "SANDBOX_BASE_URL",
    "STATUS_PATH",
    "TRANSACTION_PATH",
    "Client",
    "GatewayError",
    "GatewayStatusError",
    "MissingTransportError",
    "Response",
    "ResponseMismatchError",
    "ResponseVerificationError",
    "TransactionRefused",
    "Transport",
]

#: Test environment. Payments made here are not real.
SANDBOX_BASE_URL = "https://pay-accept.bm.pl"

#: Live environment.
PRODUCTION_BASE_URL = "https://pay.bm.pl"

#: Endpoint a transaction request is posted to.
TRANSACTION_PATH = "/payment"

#: Endpoint a refund request is posted to.
REFUND_PATH = "/transactionRefund"

#: Endpoint a cancellation request is posted to.
CANCEL_PATH = "/transactionCancel"

#: Endpoint a status query is posted to.
STATUS_PATH = "/transactionStatus"

DEFAULT_TIMEOUT = 30.0

XML_CONTENT_TYPE = "application/xml; charset=UTF-8"

_Request = TypeVar("_Request", bound=_Document)
_Answer = TypeVar("_Answer", bound=_Document)


class GatewayError(RuntimeError):
    """The gateway answered, but not with a document worth acting on."""


class GatewayStatusError(GatewayError):
    """The gateway answered with a status outside the 2xx range."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"the gateway answered with HTTP {status_code}")


class ResponseVerificationError(GatewayError):
    """The answer parsed, but its digest is not the one the key produces."""

    def __init__(self, response: Any) -> None:
        self.response = response
        super().__init__("the gateway's answer does not verify with the shared key")


class ResponseMismatchError(GatewayError):
    """The answer verifies, but it answers another request.

    A verified answer still has to belong to the call that was made: one
    naming another order, or echoing another message, reports a different
    payment and would otherwise be acted on as if it reported this one.
    """

    def __init__(
        self, field: str, expected: str | None, found: str | None, response: Any
    ) -> None:
        self.field = field
        self.expected = expected
        self.found = found
        self.response = response
        super().__init__(
            f"the gateway answered {field} {found!r} "
            f"to a request naming {expected!r}"
        )


class TransactionRefused(GatewayError):
    """The gateway refused to start the payment, and said why.

    The refusal is verified before it is raised, so the reason this carries was
    signed with the shared key rather than supplied by whoever answered.
    """

    def __init__(self, refusal: TransactionRefusal) -> None:
        self.refusal = refusal
        self.order_id = refusal.order_id
        self.reason = refusal.reason
        super().__init__(
            f"the gateway refused order {refusal.order_id}: "
            f"{refusal.reason or refusal.confirmation}"
        )


class MissingTransportError(RuntimeError):
    """No HTTP client was given and none could be built."""


class Response(Protocol):
    """The part of an HTTP response this client reads."""

    status_code: int
    text: str


class Transport(Protocol):
    """The part of an HTTP client this client calls."""

    def post(
        self,
        url: str,
        *,
        content: bytes,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Response: ...


def _httpx_transport(timeout: float) -> Any:
    try:
        import httpx
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on the env
        raise MissingTransportError(
            "no HTTP client was given and httpx is not installed: "
            "pass http=... or install bluemedia[httpx]"
        ) from exc
    return httpx.Client(timeout=timeout)


def _identifier(value: str | None) -> str:
    return value.strip() if value is not None else ""


class Client:
    """A configured connection to one Blue Media service.

    The service id and the shared key belong to the same service, so they are
    held together here and the caller never repeats them per request.
    """

    def __init__(
        self,
        service_id: str,
        shared_key: str,
        *,
        base_url: str = SANDBOX_BASE_URL,
        http: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        separator: str = DEFAULT_SEPARATOR,
        algorithm: str = DEFAULT_ALGORITHM,
        verify_responses: bool = True,
    ) -> None:
        if not service_id:
            raise ValueError("a service id is required")
        if not shared_key:
            raise ValueError("the shared key must not be empty")
        self.service_id = str(service_id)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.separator = separator
        self.algorithm = algorithm
        self.verify_responses = verify_responses
        self._key = shared_key
        self._http = http
        self._owned: Any = None

    @property
    def transport(self) -> Transport:
        if self._http is None:
            self._owned = _httpx_transport(self.timeout)
            self._http = self._owned
        return self._http

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def prepare(
        self, request: TransactionStart | None = None, /, **fields: Any
    ) -> TransactionStart:
        """Return the request the gateway would receive, signed, without sending.

        Given field values rather than a request, the service id is filled in
        from the client.
        """
        if request is not None and fields:
            raise TypeError(
                "pass either a TransactionStart or its fields, not both"
            )
        if request is None:
            fields.setdefault("service_id", self.service_id)
            request = TransactionStart(**fields)
        return request.signed(
            self._key, separator=self.separator, algorithm=self.algorithm
        )

    def start(
        self, request: TransactionStart | None = None, /, **fields: Any
    ) -> TransactionInit:
        """Sign a transaction request, post it, and read the gateway's answer.

        The answer is verified with the same key unless the client was built
        with ``verify_responses=False``; an answer that does not verify is an
        error rather than a value, because its redirection URL is where a payer
        would be sent. For the same reason it has to be the answer to this
        request: one naming another order raises ``ResponseMismatchError``,
        whether or not the client verifies digests.

        A refused start answers with a refusal instead of a payment. It is read
        and verified the same way and then raised as ``TransactionRefused``, so
        the reason reaches the caller; a refusal whose digest nobody with the
        key could have produced stays a ``ResponseVerificationError``.
        """
        signed = self.prepare(request, **fields)
        body = _parse(self.post(TRANSACTION_PATH, signed.to_xml()))
        if TransactionRefusal.refuses(body):
            refusal = self._verified(TransactionRefusal.from_xml(body))
            self._match(refusal, "orderID", signed.order_id, refusal.order_id)
            raise TransactionRefused(refusal)
        answer = self._verified(TransactionInit.from_xml(body))
        self._match(answer, "orderID", signed.order_id, answer.order_id)
        return answer

    def blik(self, code: str | None = None, /, **fields: Any) -> TransactionInit:
        """Start a payment on the BLIK gateway, with the payer's code or without.

        With the code, the gateway authorises the payment itself and the answer
        carries a status instead of a redirection URL: the payer confirms the
        amount in the banking application and the outcome arrives later, as a
        notification. Without it, the answer redirects to the screen where the
        payer enters the code.
        """
        fields.setdefault("service_id", self.service_id)
        return self.start(blik_transaction(code=code, **fields))

    def refund(
        self, request: RefundRequest | None = None, /, **fields: Any
    ) -> RefundResponse:
        """Send a payment back, whole or in part.

        With ``amount``, only that much goes back and the rest of the payment
        stands; without it, the gateway returns everything it took. The payment
        is named by ``remote_id`` or by ``order_id``, and a ``message_id`` is
        made up when the caller does not bring one.
        """
        signed = self._operation(RefundRequest, request, fields)
        return self._exchange(
            REFUND_PATH, signed, RefundResponse, signed.message_id
        )

    def cancel(
        self, request: CancelRequest | None = None, /, **fields: Any
    ) -> CancelResponse:
        """Call off a payment the gateway has not settled yet."""
        signed = self._operation(CancelRequest, request, fields)
        return self._exchange(
            CANCEL_PATH, signed, CancelResponse, signed.message_id
        )

    def status(
        self, request: StatusRequest | None = None, /, **fields: Any
    ) -> NotificationList:
        """Ask where a payment stands.

        The gateway answers a query with the document it pushes as an ITN, so
        the answer comes back as a ``NotificationList`` and every payment in it
        verifies on its own; one that does not is refused rather than returned.
        """
        signed = self._operation(StatusRequest, request, fields)
        answer = NotificationList.from_xml(self.post(STATUS_PATH, signed.to_xml()))
        if self.verify_responses:
            for notification in answer:
                if not notification.verify(
                    self._key, separator=self.separator, algorithm=self.algorithm
                ):
                    raise ResponseVerificationError(notification)
        return answer

    def post(self, path: str, document: str) -> str:
        """Post one XML document and return the body of the answer."""
        response = self.transport.post(
            self.url(path),
            content=document.encode("utf-8"),
            headers={"Content-Type": XML_CONTENT_TYPE, "Accept": "application/xml"},
            timeout=self.timeout,
        )
        if not 200 <= response.status_code < 300:
            raise GatewayStatusError(response.status_code, response.text)
        return response.text

    def close(self) -> None:
        """Close the HTTP client, if this client is the one that opened it."""
        if self._owned is not None:
            self._owned.close()
            self._owned = None
            self._http = None

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _operation(
        self,
        model: type[_Request],
        request: _Request | None,
        fields: dict[str, Any],
    ) -> _Request:
        if request is not None and fields:
            raise TypeError(
                f"pass either a {model.__name__} or its fields, not both"
            )
        if request is None:
            fields.setdefault("service_id", self.service_id)
            fields.setdefault("message_id", new_message_id())
            request = model(**fields)
        return request.signed(
            self._key, separator=self.separator, algorithm=self.algorithm
        )

    def _verified(self, answer: _Answer) -> _Answer:
        if self.verify_responses and not answer.verify(
            self._key, separator=self.separator, algorithm=self.algorithm
        ):
            raise ResponseVerificationError(answer)
        return answer

    def _match(
        self,
        answer: _Document,
        field: str,
        expected: str | None,
        found: str | None,
    ) -> None:
        if _identifier(found) != _identifier(expected):
            raise ResponseMismatchError(field, expected, found, answer)

    def _exchange(
        self,
        path: str,
        signed: _Document,
        answer_type: type[_Answer],
        message_id: str,
    ) -> _Answer:
        answer = self._verified(
            answer_type.from_xml(self.post(path, signed.to_xml()))
        )
        self._match(answer, "messageID", message_id, answer.message_id)
        return answer
