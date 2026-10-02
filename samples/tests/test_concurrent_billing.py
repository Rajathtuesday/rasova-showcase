# Excerpt from Rasova (private repository): orders/tests/test_bill_numbers.py (the fixture and the concurrency tests)
# Shown as written in production. It imports from the rest of the codebase,
# so it is for reading, not for running on its own.

import threading
from decimal import Decimal as D
from unittest import skipUnless

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounts.models import User
from core.utils import get_business_date
from menu.models import MenuCategory, MenuItem
from orders.models import BillSeries, Order, OrderItem
from orders.services.bill_numbers import financial_year
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant


class BillWorld(TestCase):
    """Spice Garden: an outlet with a GSTIN, a table and a ₹100 dosa at 5%."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Spice Garden")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Indiranagar", gst_no=SAMPLE_GSTIN)
        self.owner = User.objects.create_user(username="bill_no_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Tiffin")
        self.dosa = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                            name="Dosa", price=D("100"), gst_percentage=D("5"))
        self.year = financial_year(get_business_date(timezone.now(), self.outlet))

    def order(self, outlet=None, **fields):
        outlet = outlet or self.outlet
        order = Order.objects.create(tenant=self.tenant, outlet=outlet, created_by=self.owner,
                                     source="counter", **fields)
        OrderItem.objects.create(order=order, menu_item=self.dosa, quantity=1, price=D("100"),
                                 gst_percentage=D("5"), total_price=D("100"))
        return order

    def bill(self, order, status="billing"):
        """The Bill button (billing) or a settled payment (closed): the save
        that bills the order."""
        order.status = status
        order.save(update_fields=["status"])
        return order.bill_number


@skipUnless(connection.vendor == "postgresql", "row locks are tested on Postgres")
class ConcurrentBillingTest(TransactionTestCase):
    """Screens billing at the same moment, on real row locks."""

    def setUp(self):
        BillWorld.setUp(self)

    order = BillWorld.order

    def _all_at_once(self, jobs):
        start = threading.Barrier(len(jobs))
        errors = []

        def run(job):
            try:
                start.wait()
                job()
            except Exception as exc:          # reported below, not lost in the thread
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(job,)) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])

    def test_bills_billed_together_get_consecutive_numbers(self):
        orders = [self.order() for _ in range(8)]

        def bill(order_id):
            def job():
                order = Order.objects.get(pk=order_id)
                order.status = "billing"
                order.save(update_fields=["status"])
            return job

        self._all_at_once([bill(order.pk) for order in orders])
        numbers = sorted(Order.objects.filter(pk__in=[o.pk for o in orders]).values_list("bill_number", flat=True))
        self.assertEqual(numbers, [f"SG/{self.year}/{n:06d}" for n in range(1, 9)])

    def test_one_bill_billed_on_two_screens_gets_one_number(self):
        order = self.order()

        def bill():
            copy = Order.objects.get(pk=order.pk)
            copy.status = "billing"
            copy.save(update_fields=["status"])

        self._all_at_once([bill, bill])
        self.assertEqual(Order.objects.get(pk=order.pk).bill_number, f"SG/{self.year}/000001")
        self.assertEqual(BillSeries.objects.get(outlet=self.outlet).last_number, 1)

