# Code samples

Six files from Rasova, exactly as they run in production. They were picked to show how I write code that has to be right: what each piece trusts, what it refuses, and how it is proven. Each imports from the rest of the private codebase, so they are for reading, not for running on their own.

| File | What it shows |
|---|---|
| [bill_numbers.py](bill_numbers.py) | Legal bill numbering (CGST rule 46: at most 16 characters, unique in the financial year). One series per outlet per year, issued under a row lock, so two screens billing at once never get the same number and none is skipped. |
| [client_ip.py](client_ip.py) | Finding the real visitor's IP behind Cloudflare and nginx. Every header can be forged, so each one is believed only from the machine that is trusted to set it. Rate limits, login lockouts and the webhook allowlist all depend on this one function. |
| [aggregator_webhook.py](aggregator_webhook.py) | How Zomato and Swiggy orders are signed: an HMAC of the timestamp and the body together, so a captured request can't be replayed with a new time, and a constant-time comparison. |
| [rate_limit_keys.py](rate_limit_keys.py) | Who a rate limit counts. Counting by IP made every guest on one restaurant's Wi-Fi share a single allowance; now each table counts on its own, with a looser cap per IP behind it. |
| [tests/test_concurrent_billing.py](tests/test_concurrent_billing.py) | Testing the bill numbers under real concurrency: threads released together by a barrier, against PostgreSQL row locks, with errors collected from the threads rather than lost. |
| [tests/test_bill_properties.py](tests/test_bill_properties.py) | Property tests with Hypothesis: rules every bill must obey, tried on thousands of random bills, deterministic in CI so a red build always means the code changed. |

A note on style: the comments explain *why*, in plain words, including the bug that made the code look the way it does. I write them for the next person to read the file, who is usually me, three months later.
