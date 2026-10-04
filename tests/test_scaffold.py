"""Smoke checks for the package layout; add behavior tests with implementations."""

from inventory.access_control.models import Role
from inventory.auth.service import Authentication
from inventory.catalog.models import Product, Supplier
from inventory.concurrency.scheduler import RequestScheduler
from inventory.persistence.repositories.products import ProductRepository
from inventory.transactions.models import Sale


def test_core_modules_are_importable() -> None:
    assert all((Role, Authentication, Product, Supplier, RequestScheduler, ProductRepository, Sale))
