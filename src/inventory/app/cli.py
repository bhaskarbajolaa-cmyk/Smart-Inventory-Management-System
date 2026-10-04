"""Interactive integration surface for the inventory system."""

from collections.abc import Callable

from inventory.access_control.models import Role
from inventory.app.services import ApplicationServices
from inventory.app.workflows import submit_sale_job
from inventory.auth.models import Session, User
from inventory.catalog.filters import ProductFilter


class InventoryCLI:
    """Route menu actions through application services and domain contracts."""

    def __init__(self, services: ApplicationServices) -> None:
        self.services = services
        self.session: Session | None = None

    def run(self) -> None:
        """Keep presenting the menu until the operator exits."""
        while True:
            print("\nSmart Inventory Management")
            print("1. Log in")
            print("2. Browse products")
            print("3. Record a sale")
            print("4. Run concurrent-sale demo")
            print("5. Log out")
            print("q. Quit")
            choice = input("Select an option: ").strip().lower()

            if choice == "1":
                self._run_action(self._login)
            elif choice == "2":
                self._run_action(self._browse_products)
            elif choice == "3":
                self._run_action(self._record_sale)
            elif choice == "4":
                self._run_action(self._concurrency_demo)
            elif choice == "5":
                self._run_action(self._logout)
            elif choice == "q":
                self._run_action(self.services.scheduler.shutdown_gracefully)
                return
            else:
                print("Choose one of the listed options.")

    def _login(self) -> None:
        username = input("Username: ").strip()
        password = input("Password: ")
        self.session = self.services.authentication.login(username, password)
        print(f"Logged in as user {self.session.user_id}.")

    def _logout(self) -> None:
        session = self._require_session()
        self.services.authentication.logout(session)
        self.session = None
        print("Logged out.")

    def _browse_products(self) -> None:
        self._require_permission("VIEW", "products")
        search_term = input("Search term (blank for all products): ").strip() or None
        products = self.services.product_repository.find_all(
            ProductFilter(search_term=search_term)
        )
        for product in products:
            print(
                f"{product.product_id}: {product.name} | SKU {product.sku} | "
                f"stock {product.quantity} | price {product.unit_price}"
            )
        if not products:
            print("No matching products.")

    def _record_sale(self) -> None:
        self._require_permission("APPEND", "sales")
        product_id = int(input("Product ID: "))
        quantity = int(input("Quantity: "))
        customer_ref = input("Customer reference: ").strip()
        job_id = self._enqueue_sale(product_id, quantity, customer_ref)
        print(f"Sale job submitted: {job_id}")

    def _concurrency_demo(self) -> None:
        self._require_permission("APPEND", "sales")
        product_id = int(input("Shared product ID: "))
        job_count = int(input("Number of simultaneous sales (minimum 5): "))
        quantity = int(input("Units per sale: "))
        if job_count < 5 or quantity < 1:
            raise ValueError("Use at least five sales and a positive quantity per sale.")

        job_ids = [
            self._enqueue_sale(product_id, quantity, f"concurrency-demo-{index}")
            for index in range(job_count)
        ]
        if not self.services.scheduler.wait_for_idle(timeout_seconds=30):
            raise TimeoutError("Timed out while waiting for sale jobs to finish.")

        product = self.services.product_repository.find_by_id(product_id)
        print(f"Completed {len(job_ids)} jobs. Final stock: {product.quantity}")

    def _enqueue_sale(self, product_id: int, quantity: int, customer_ref: str) -> str:
        session = self._require_session()
        user, role = self._current_user_and_role(session)
        return submit_sale_job(
            self.services,
            user,
            role,
            product_id,
            quantity,
            customer_ref,
        )

    def _require_permission(self, code: str, table_name: str) -> tuple[User, Role]:
        session = self._require_session()
        user, role = self._current_user_and_role(session)
        if not user.has_permission(code, role):
            raise PermissionError(f"The current user lacks {code} on {table_name}.")
        return user, role

    def _current_user_and_role(self, session: Session) -> tuple[User, Role]:
        user = self.services.user_repository.find_by_id(session.user_id)
        if user is None:
            raise LookupError("The session user no longer exists.")
        role = self.services.role_repository.find_by_id(user.role_id)
        if role is None:
            raise LookupError("The user's assigned role no longer exists.")
        return user, role

    def _require_session(self) -> Session:
        if self.session is None or self.session.is_expired():
            self.session = None
            raise PermissionError("Log in with a valid session first.")
        return self.session

    @staticmethod
    def _run_action(action: Callable[[], object]) -> None:
        try:
            action()
        except NotImplementedError:
            print("This path reaches a class method whose TODO is not implemented yet.")
        except (LookupError, PermissionError, TimeoutError, ValueError) as error:
            print(error)
