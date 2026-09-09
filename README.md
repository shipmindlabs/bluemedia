# bluemedia

Python client for the Blue Media payment gateway: XML protocol, SHA-256 signing,
BLIK, ITN and recurring notifications.

## Status

Early. The package can build the string a message is signed over, hash it with
the shared key, read and write the transaction documents, start a transaction
against the gateway, take a BLIK payment, and read and answer ITN
notifications; recurring payments are next.

## Installation

```bash
pip install bluemedia
```

Signing and parsing need nothing but the standard library. Sending needs an HTTP
client: either install `bluemedia[httpx]` or pass your own.

## The string to sign

Blue Media does not hash a serialised document. It hashes the values of a fixed
set of fields joined by `|`, taken in the order the specification declares --
which is neither document order nor alphabetical order. That order is data here,
not control flow: `TRANSACTION_FIELD_ORDER` for outgoing requests,
`TRANSACTION_INIT_FIELD_ORDER` for the gateway's answer and `ITN_FIELD_ORDER`
for incoming notifications.

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

`Client` holds one service's id and shared key, builds the request, signs it,
posts it and verifies the answer:

```python
from bluemedia import Client

with Client("123456", "shared-key") as client:
    answer = client.start(order_id="5555", amount="10.99", currency="PLN")

answer.redirect_url
# 'https://pay.example.com/AB-1'
```

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

An ITN arrives base64-encoded in a form field, reports every payment the gateway
has to tell the shop about in one document, and has to be answered in the same
request, order by order:

```python
from bluemedia import NotificationList

itn = NotificationList.from_base64(form["transactions"])
reply = itn.confirm("shared-key")

reply.to_xml()
# '<confirmationList><serviceID>123456</serviceID>…</confirmationList>'
```

`confirm()` verifies each notification, answers `CONFIRMED` for the ones that
verify and `NOTCONFIRMED` for the ones that do not, and signs the reply with the
same key. Booking the payment is the shop's business, so `accept=` decides it:

```python
reply = itn.confirm("shared-key", accept=lambda n: book(n.order_id, n.amount))
```

The callback is only asked about notifications whose digest already verifies, so
a forged one cannot become `CONFIRMED` by way of a callback that trusts what it
is handed. Answering `NOTCONFIRMED` is also why a bad notification is not simply
dropped: an unanswered one is only sent again.

`serviceID` is written once, on the list, but signs every notification inside
it, so each `Notification` is given it while parsing and verifies on its own.
`from_xml()` takes an already decoded document; a payload that is not base64
raises `Base64DecodeError`, one more subclass of `XMLParseError`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT, see [LICENSE](LICENSE).

Maintained by [Shipmind Labs](https://shipmindlabs.com).
