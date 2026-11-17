from sqlalchemy.orm import Session
from app.schemas.customer import CustomerCreate, CustomerUpdate
from app.services.customer_service import CustomerService


class CustomerController:

    @staticmethod
    def create_customer(db: Session, data: CustomerCreate):
        return CustomerService.create_customer(db, data)

    @staticmethod
    def get_customers(db: Session, skip: int = 0, limit: int = 50):
        return CustomerService.get_customers(db, skip, limit)

    @staticmethod
    def get_customer(db: Session, customer_id: int):
        return CustomerService.get_customer(db, customer_id)

    @staticmethod
    def update_customer(
        db: Session,
        customer_id: int,
        data: CustomerUpdate
    ):
        return CustomerService.update_customer(db, customer_id, data)

    @staticmethod
    def deactivate_customer(db: Session, customer_id: int):
        return CustomerService.deactivate_customer(db, customer_id)