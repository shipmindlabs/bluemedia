# bluemedia

Python client for the Blue Media payment gateway: XML protocol, SHA-256 signing,
BLIK, ITN and recurring notifications.

## Status

Early. The package can build the string a message is signed over, hash it with
the shared key, read and write the transaction documents, and start a
transaction against the gateway; notifications are next.

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

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT, see [LICENSE](LICENSE).

Maintained by [Shipmind Labs](https://shipmindlabs.com).
