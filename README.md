# bluemedia

Python client for the Blue Media payment gateway: XML protocol, SHA-256 signing,
BLIK, ITN and recurring notifications.

## What Blue Media is, and why this exists

Blue Media is a Polish payment provider. A shop posts it a transaction, the
payer is taken to a bank transfer, a card form or BLIK, and the outcome comes
back to the shop as a notification the shop has to answer. The protocol itself
is small: a handful of XML documents over HTTPS, no SDK, no session, no tokens.

What is not small is the signature. Blue Media does not sign a serialised
document; it signs the *values* of a fixed set of fields, joined by `|`, taken
in the order the specification declares field by field -- which is neither
document order nor alphabetical order -- with the shared key appended as one
more part. Empty fields drop out, the hash field never signs itself, and one
field in the wrong place changes the digest completely. The gateway answers a
wrong digest by refusing the request, without saying which of thirty-odd fields
moved.

So this package keeps the orders as data, pinned by table-driven tests; gives
each document a typed model that knows its own order, so signing and verifying
need no bookkeeping from the caller; parses incoming documents strictly,
because a payment document is untrusted input; verifies every answer before
handing it back; and defaults to the sandbox, so a client nobody configured
cannot move real money.

## Status

Early. The package can build the string a message is signed over, hash it with
the shared key, read and write the transaction documents, start a transaction
against the gateway, take a BLIK payment, read and answer ITN notifications --
the recurring ones among them -- and refund, cancel or query a payment it
already made.

## Installation

```bash
pip install bluemedia
```

Signing and parsing need nothing but the standard library. Sending needs an HTTP
client: either install `bluemedia[httpx]` or pass your own.

## Quickstart: a payment in the sandbox

The service id and the shared key come from a Blue Media test account; the two
below are the ones this README signs its examples with. `Client` builds the
request, signs it, posts it to the sandbox and verifies what comes back.

```python
from bluemedia import Client

SERVICE_ID = "123456"
SHARED_KEY = "9dfc5eb3d1b2f4e0"

with Client(SERVICE_ID, SHARED_KEY) as client:
    answer = client.start(
        order_id="5555",
        amount="10.99",
        currency="PLN",
        description="Order 5555",
        return_url="https://shop.example.com/return",
    )

print(answer.remote_id)      # 'AB-1'   the gateway's own name for the payment
print(answer.payment_status) # 'PENDING'
print(answer.redirect_url)   # 'https://pay-accept.bm.pl/…' send the payer here
```

That call needs httpx (`pip install "bluemedia[httpx]"`) and a sandbox account.
Without either, the same request can be built and signed offline, which is
enough to check a service's configuration against the specification:

```python
from bluemedia import Client

client = Client("123456", "9dfc5eb3d1b2f4e0")
request = client.prepare(order_id="5555", amount="10.99", currency="PLN")

print(request.to_xml())
# '<transaction><serviceID>123456</serviceID>…<hash>…</hash></transaction>'
print(request.verify("9dfc5eb3d1b2f4e0"))
# True
```

The redirection URL is the whole point of the answer to a card or transfer
payment: the payer goes there, pays at their bank, and returns to `returnURL`.
The order is not paid at that moment, though. It is paid when the ITN arrives.

## Quickstart: the ITN webhook

An ITN is the notification the gateway pushes when a payment changes state. It
arrives base64-encoded in a form field, reports every payment the gateway has
to tell the shop about in one document, and has to be answered in the same HTTP
response, order by order -- an unanswered notification is simply sent again.

```python
from flask import Flask, Response, request

from bluemedia import SUCCESS, NotificationList

app = Flask(__name__)
SHARED_KEY = "9dfc5eb3d1b2f4e0"


def book(notification):
    """Take the payment, or refuse it, and say which."""
    if notification.payment_status != SUCCESS:
        return False
    return mark_order_paid(notification.order_id, notification.amount)


@app.post("/bluemedia/itn")
def itn():
    notifications = NotificationList.from_base64(request.form["transactions"])
    reply = notifications.confirm(SHARED_KEY, accept=book)
    return Response(reply.to_xml(), mimetype="application/xml")
```

`confirm()` verifies each notification, asks `accept` about the ones that
verify, answers `CONFIRMED` or `NOTCONFIRMED` per order and signs the reply with
the same key. `book()` is only ever called for a notification whose digest
already checks out, so a forged one cannot be booked by a callback that trusts
what it is handed; and returning false for a payment the shop cannot take
leaves the gateway sending it again rather than considering it settled.

The rest of this README is the detail behind those two examples.

## The string to sign

The order is data here, not control flow: `TRANSACTION_FIELD_ORDER` for outgoing
requests, `TRANSACTION_INIT_FIELD_ORDER` for the gateway's answer and
`ITN_FIELD_ORDER` for incoming notifications.

```python
from bluemedia import TRANSACTION_FIELD_ORDER, string_to_sign

string_to_sign(
    {
        "Amount": "10.99",
        "OrderID": "5555",
        "Currency": "PLN",
        "ServiceID": "123456",
        "GatewayID": "21",
    },
    TRANSACTION_FIELD_ORDER,
)
# '123456|5555|10.99|21|PLN'
```

Field names match regardless of case, empty and missing values drop out, a field
holding several values expands in place, and the `hash` field is skipped because
it carries the result rather than an input. A field the declared order does not
mention raises `UnknownFieldError` instead of quietly disappearing from the
string.

Use `ordered_values()` when the parts are more useful than the joined string.

## Hashing and verifying

The shared key is appended to the ordered values as one more part, and the whole
thing is hashed. `sign()` returns the lowercase hex digest that belongs in the
`hash` field:

```python
from bluemedia import ITN_FIELD_ORDER, TRANSACTION_FIELD_ORDER, sign, verify

sign(request, TRANSACTION_FIELD_ORDER, "shared-key")
# '…64 hex characters…'

verify(notification, ITN_FIELD_ORDER, "shared-key")
# True
```

`verify()` recomputes the digest and compares it in constant time. It reads the
digest from the message's `hash` field, or takes one through `digest=`, and
tolerates surrounding whitespace and uppercase because gateways deliver both. A
message carrying no digest is unverified rather than an error, so every
rejection -- wrong key, wrong field order, truncated payload, missing hash --
looks the same to the caller: `False`.

Services configured for another digest algorithm pass `algorithm="sha512"` or
any other name `hashlib` accepts; the default is SHA-256.

## Typed messages

`TransactionStart` is a transaction request and `TransactionInit` is the answer
the gateway returns to one. Both are frozen dataclasses that know their own
field order, so signing and verifying need no bookkeeping from the caller:

```python
from bluemedia import TransactionInit, TransactionStart

request = TransactionStart(
    service_id="123456",
    order_id="5555",
    amount="10.99",
    currency="PLN",
    gateway_id="21",
).signed("shared-key")

request.to_xml()
# '<transaction><serviceID>123456</serviceID>…</transaction>'

answer = TransactionInit.from_xml(payload)
answer.verify("shared-key")
# True
answer.redirect_url
# 'https://pay.example.com/AB-1'
```

Every attribute carries the specification's name once, in its field metadata,
which is how `redirecturl` -- lowercase in a document otherwise written in
camelCase -- reaches Python as `redirect_url`.

Parsing is strict, because a payment document is untrusted input. An element the
model does not declare, an element sent twice, a required element left out or
left empty: each raises a subclass of `XMLParseError` rather than producing a
half-filled object that would only fail later, at the digest. Values stay text
throughout, since the gateway signs the exact characters it sent and `10.90`
must not come back as `10.9`.

## Starting a transaction

The base URL defaults to the sandbox, `SANDBOX_BASE_URL`; live traffic is
`base_url=PRODUCTION_BASE_URL`, spelled out rather than defaulted, so an
unconfigured client cannot move real money. `timeout=` bounds every call.

Transport is injected. `http=` takes anything with httpx's
`post(url, content=..., headers=..., timeout=...)`, which is where a shared
session, retries, a proxy or instrumentation belong -- and what the test suite
uses instead of a network. Left out, an `httpx.Client` is created on first use
and closed with the client.

The answer is parsed and its digest checked against the same key; one that does
not verify raises `ResponseVerificationError` rather than handing back a
redirection URL of unknown origin. A non-2xx status raises `GatewayStatusError`,
which carries the status and the body, and a body that is not a transaction
document raises `XMLParseError`.

Use `prepare()` to get the signed request without sending it -- useful for a
call the client does not make yet, or for a golden test against the
specification.

### A refused start

The gateway can also answer a start by refusing it, and a refusal is a different
document under the same `transaction` root: no `remoteID` and nowhere to send a
payer, a `confirmation` saying the order was not taken and a `reason` saying
why. It is told apart by the element only a refusal carries, read as a
`TransactionRefusal`, verified with the same key, and raised:

```python
from bluemedia import TransactionRefused

try:
    answer = client.start(order_id="5555", amount="10.99", currency="PLN")
except TransactionRefused as refused:
    refused.reason    # 'transaction limit exceeded'
    refused.order_id  # '5555'
    refused.refusal   # the verified TransactionRefusal itself
```

So a refusal is an outcome with a reason rather than an element the answer does
not declare. `TransactionRefused` is a `GatewayError`, which is what a caller
that only cares that the payment did not start can catch.

The refusal is verified before it is raised, so the reason it carries was signed
with the shared key rather than supplied by whoever answered: one whose digest
the key does not produce stays a `ResponseVerificationError`, because a forged
refusal would otherwise tell a shop whatever it liked about an order. It signs
in its own order, `TRANSACTION_REFUSAL_FIELD_ORDER`, which is the four fields it
carries and not a shortened answer.

## BLIK

BLIK is a gateway rather than a second protocol: the same transaction document,
sent to gateway `509`, which is `BLIK_GATEWAY_ID`. What it adds is the six-digit
code the payer reads out of a banking application.

```python
from bluemedia import Client

with Client("123456", "shared-key") as client:
    answer = client.blik("777 123", order_id="5555", amount="10.99", currency="PLN")

answer.payment_status
# 'PENDING'
answer.redirect_url
# None
```

That is what differs from a card payment. With a code, the gateway authorises
the payment itself and there is nowhere to send the payer: the answer carries a
status instead of a redirection URL, the payer accepts the amount in the banking
application, and the outcome arrives afterwards as an ITN -- so the shop books
the order there, not on the answer. A card payment is the other way round: its
answer is worth having only for the URL the payer is redirected to.

Leave the code out and BLIK behaves like every other gateway again: the answer
redirects to a screen where the payer enters the code, and `returnURL` brings
them back.

A code is six digits, lives about two minutes and works once. It signs in the
place `TRANSACTION_FIELD_ORDER` gives `AuthorizationCode`, like any other field,
which is why the grouping a payer copies along with it -- `777 123`, `777-123`
-- is stripped before the digest is taken. Anything that is not six digits
raises `AuthorizationCodeError` here rather than at the gateway.

`blik_transaction()` builds the same request without sending it, for `prepare()`
or for a caller that posts it elsewhere. The alias fields -- `blik_uid_key`,
`blik_alias_key` and their labels -- travel on that request too, for a service
that registers the payer to pay later without a code.

## Notifications

Booking the payment is the shop's business, so `accept=` decides it, and the
callback is only asked about notifications whose digest already verifies:

```python
from bluemedia import NotificationList

itn = NotificationList.from_base64(form["transactions"])
reply = itn.confirm("shared-key", accept=lambda n: book(n.order_id, n.amount))

reply.to_xml()
# '<confirmationList><serviceID>123456</serviceID>…</confirmationList>'
```

Without `accept`, everything that verifies is confirmed. Answering
`NOTCONFIRMED` is why a bad notification is not simply dropped: an unanswered
one is only sent again. `verified()` returns the notifications that check out,
for a shop that wants to look before answering.

`serviceID` is written once, on the list, but signs every notification inside
it, so each `Notification` is given it while parsing and verifies on its own.
`from_xml()` takes an already decoded document; a payload that is not base64
raises `Base64DecodeError`, one more subclass of `XMLParseError`.

### Recurring payments

A recurring payment reports one element more, and that one holds elements
rather than text:

```xml
<recurringData>
  <recurringAction>INIT</recurringAction>
  <clientHash>CLIENT-HASH</clientHash>
</recurringData>
```

`recurring_data` reads it as a `RecurringData` of its own, as strictly as the
notification around it: a missing action, a part the model does not declare, or
an element carrying text where parts belong all raise an `XMLParseError`
instead of reaching the digest half-read.

```python
payment = itn[0]

payment.recurring_data.recurring_action
# 'INIT'       what the gateway did to the subscription
payment.recurring_data.client_hash
# 'CLIENT-HASH'  what the next charge is made under
payment.verify("shared-key")
# True
```

The nested document never signs on its own. Its parts take the single place
`recurringData` has in `ITN_FIELD_ORDER` -- last -- one after another, in the
order `RECURRING_DATA_FIELD_ORDER` declares, which is the flatten rule every
multi-valued field already follows. So a recurring notification signs over two
parts more than the same payment without one, a `clientHash` left out simply
drops a part, and a plain notification signs exactly as it did before.

## Refunds, cancellation and status

Three calls act on a payment that already exists. Each names it by the gateway's
own `remote_id` or by the shop's `order_id`, and each carries a `message_id` the
gateway echoes back, so an answer can be matched to the call that caused it; one
is made up when the caller brings none.

```python
from bluemedia import Client

with Client("123456", "shared-key") as client:
    answer = client.refund(remote_id="AB-1", amount="5.00", currency="PLN")

answer.remote_out_id
# 'AB-1-R'
answer.succeeded
# True
```

A refund is partial when it names an amount and whole when it does not: the
amount is a field like any other, so leaving it out keeps it out of the document
and out of the digest, and the gateway sends back everything it took. That the
difference between the two is one absent field is why an amount which is present
but blank raises `RefundAmountError` -- an empty form field must not quietly
turn a partial refund into a full one. On the answer, `remote_out_id` identifies
the refund itself, which is not the identifier of the payment it came out of.

`succeeded` means `SUCCESS` and nothing else. A refund the gateway accepted but
has not finished answers `PENDING` and reports its outcome later, as an ITN, so
read `refund_status` where the difference between refused and not yet matters;
`refund_reason` says why a `FAILURE` was refused. `cancel()` calls off a payment
the gateway has not settled and its answer reads the same way, through
`cancel_status`, `cancel_reason` and `succeeded`.

`status()` asks where a payment stands. The gateway answers a query with the
document it pushes as an ITN, so the answer is a `NotificationList` and every
payment in it verifies on its own; one that does not raises
`ResponseVerificationError` rather than coming back as a payment to book.

```python
answer = client.status(order_id="5555")
answer[0].payment_status
# 'SUCCESS'
```

A request naming no payment -- neither identifier, or one holding nothing --
raises `MissingReferenceError` before anything is sent, because at the gateway
it would apply to nothing. Each request signs in its own declared order,
`REFUND_FIELD_ORDER`, `CANCEL_FIELD_ORDER` and `TRANSACTION_STATUS_FIELD_ORDER`,
and goes to its own endpoint; `RefundRequest`, `CancelRequest` and
`StatusRequest` build and sign the same documents for a caller that posts them
elsewhere.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT, see [LICENSE](LICENSE).

Maintained by [Shipmind Labs](https://shipmindlabs.com).
