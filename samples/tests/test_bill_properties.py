# Excerpt from Rasova (private repository): orders/tests/test_totals_properties.py (the first rules)
# Shown as written in production. It imports from the rest of the codebase,
# so it is for reading, not for running on its own.

"""
Property tests for the bill maths: rules every bill must obey, checked on
thousands of random bills instead of a few hand-picked ones.

Two kinds of test live here:

  * Rules on the frozen copy of today's maths (legacy_totals.py): the bill
    adds up, round-off stays within 50 paise, CGST and SGST split the GST,
    composition outlets charge no GST, voided and free dishes change nothing,
    the parcel charge carries its own rate and never changes the food's tax.
    No database, so they run fast on many bills.

  * A differential test: random bills go through the real
    Order.recalculate_totals() on real rows and must match the frozen copy to
    the paisa. When the liquor VAT work rewrites the maths, this is what
    catches a food-only bill that no longer totals the same, and Hypothesis
    shrinks the failure down to the smallest bill that shows it.

Bills with liquor (Phase 1) get the same two kinds of test, against their
own independent copy of the rules (legacy_totals.liquor_totals).

In CI the random bills are the same on every run (derandomize), so a red
build always means the code changed, never bad luck. For a deeper search:
    MONEY_THOROUGH=1 python manage.py test orders.tests.test_totals_properties
"""
import os
from decimal import ROUND_HALF_UP, Decimal as D

from django.test import SimpleTestCase
from hypothesis import HealthCheck, given, settings, strategies as st
from hypothesis.extra.django import TestCase as HypothesisTestCase

from orders.tests.legacy_totals import legacy_totals, liquor_totals
from tenants.models import Outlet
from orders.tests.money_scenarios import (
    CURRENT_GST_RATES, GST_RATES, LIQUOR_CONFIGS, LIQUOR_RATES, OUTLET_CONFIGS, build_liquor_world,
    build_world, create_orders, liquor_item, liquor_snapshot, make_item, make_spec, row_head, snapshot,
)

THOROUGH = os.environ.get("MONEY_THOROUGH") == "1"
_COMMON = dict(deadline=None, database=None, derandomize=not THOROUGH)
PURE = settings(max_examples=20_000 if THOROUGH else 500, **_COMMON)
REAL = settings(max_examples=3_000 if THOROUGH else 150,
                suppress_health_check=[HealthCheck.too_slow], **_COMMON)

INCLUSIVE_CFGS = [i for i, (inclusive, _) in enumerate(OUTLET_CONFIGS) if inclusive]
COMPOSITION_CFGS = [i for i, (_, composition) in enumerate(OUTLET_CONFIGS) if composition]
GST_CFGS = [i for i, (_, composition) in enumerate(OUTLET_CONFIGS) if not composition]


# ---------------------------------------------------------------------------
# Random bills, in the same shape as money_scenarios specs
# ---------------------------------------------------------------------------

def _paise(low, high):
    """A money string with exactly two decimals, between low and high rupees."""
    return st.integers(int(low * 100), int(high * 100)).map(lambda n: f"{D(n) / 100:.2f}")


prices = st.one_of(
    st.just("0.00"),
    st.integers(1, 60).map(lambda n: f"{n * 5}.00"),   # round menu prices
    _paise(0.01, 2500),                                # anything with paise
)
dishes = st.builds(
    make_item,
    price=prices,
    qty=st.integers(1, 25),
    gst=st.sampled_from(GST_RATES),
    disc=st.one_of(st.just("0"), _paise(0.01, 100)),
    status=st.sampled_from(["pending", "sent", "served", "served", "voided"]),
    comp=st.sampled_from([False, False, False, True]),
    modifier=st.sampled_from(["0", "0", "0", "10", "25", "49.50"]),
)
order_discounts = st.one_of(
    st.just((None, "0")),
    st.tuples(st.just("percentage"), _paise(0.01, 100)),
    st.tuples(st.just("amount"), _paise(0.01, 100000)),
)
parcels = st.one_of(st.just("0"), _paise(0.01, 500))
# None: a bill from before parcel GST, whose parcel charge carries none
parcel_rates = st.sampled_from([None, "5", "5", "18"])


@st.composite
def bills(draw, cfgs=tuple(range(len(OUTLET_CONFIGS)))):
    cfg = draw(st.sampled_from(list(cfgs)))
    dtype, dval = draw(order_discounts)
    return make_spec(cfg, draw(st.lists(dishes, max_size=10)), dtype, dval,
                     draw(parcels), draw(parcel_rates))


def live_items(spec):
    return [it for it in spec["items"] if it["status"] != "voided" and not it["comp"]]


def totals(spec):
    """legacy_totals() with the money fields as Decimals (pgst is 0 without a parcel)."""
    out = legacy_totals(spec, OUTLET_CONFIGS)
    return {"pgst": D("0"), **{key: (value if key == "bd" else D(value)) for key, value in out.items()}}


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

class BillRulesTest(SimpleTestCase):
    """Rules today's maths obeys on any bill. They must still hold after the
    liquor VAT work, for food bills and mixed bills alike."""

    @PURE
    @given(bills())
    def test_the_bill_adds_up(self, spec):
        t = totals(spec)
        inclusive, _ = OUTLET_CONFIGS[spec["cfg"]]
        parcel = D(spec["parcel"])
        if inclusive:
            # menu prices and the parcel charge already hold their GST, and
            # the discount came off the prices
            expected = t["sub"] + t["gst"] - t["pgst"] + parcel + t["ro"]
        else:
            expected = t["sub"] - t["disc"] + t["gst"] + parcel + t["ro"]
        self.assertEqual(t["grand"], expected)

    @PURE
    @given(bills())
    def test_grand_total_is_whole_rupees_and_round_off_is_at_most_fifty_paise(self, spec):
        t = totals(spec)
        self.assertEqual(t["grand"], t["grand"].to_integral_value())
        self.assertLessEqual(abs(t["ro"]), D("0.50"))

    @PURE
    @given(bills())
    def test_cgst_and_sgst_split_the_gst_evenly(self, spec):
        t = totals(spec)
        self.assertEqual(t["cgst"] + t["sgst"], t["gst"])
        self.assertIn(t["cgst"] - t["sgst"], (D("0.00"), D("0.01")))

    @PURE
    @given(bills(cfgs=COMPOSITION_CFGS))
    def test_composition_outlets_charge_no_gst(self, spec):
        t = totals(spec)
        self.assertEqual((t["gst"], t["cgst"], t["sgst"], t["pgst"]), (0, 0, 0, 0))
        self.assertEqual(t["bd"], [], "a bill of supply has no GST breakdown")

    # ... 16 more rules follow, then a differential test that sends random
    # bills through the real Order.recalculate_totals() on database rows and
    # requires the same totals to the paisa.
