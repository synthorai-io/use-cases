# shipping

A tiny library that prices shipping for an order. It exists to give a coding
agent something small to work on.

```bash
python -m unittest discover -s tests
```

One test fails on purpose: an order of exactly 50.00 should ship free inside
the domestic zone and does not.
