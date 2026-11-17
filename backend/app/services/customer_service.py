from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models.customer import Customer
from app.schemas.customer import CustomerCreate, CustomerUpdate


class CustomerService:

    @staticmethod
    def create_customer(db: Session, data: CustomerCreate):
        existing = db.query(Customer).filter(
            Customer.phone_number == data.phone_number
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Customer with this phone number already exists"
            )

        customer = Customer(
            full_name=data.full_name,
            phone_number=data.phone_number,
            email=str(data.email) if data.email else None
        )

        db.add(customer)
        db.commit()
        db.refresh(customer)
        return customer

    @staticmethod
    def get_customers(db: Session, skip: int = 0, limit: int = 50):
        return (
            db.query(Customer)
            .order_by(Customer.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_customer(db: Session, customer_id: int):
        customer = db.query(Customer).filter(
            Customer.id == customer_id
        ).first()

        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        return customer

    @staticmethod
    def update_customer(db: Session, customer_id: int, data: CustomerUpdate):
        customer = CustomerService.get_customer(db, customer_id)
        updates = data.model_dump(exclude_unset=True)

        if "phone_number" in updates and updates["phone_number"]:
            existing = db.query(Customer).filter(
                Customer.phone_number == updates["phone_number"],
                Customer.id != customer_id
            ).first()

            if existing:
                raise HTTPException(
                    status_code=409,
                    detail="Phone number already belongs to another customer"
                )

        if "email" in updates and updates["email"]:
            updates["email"] = str(updates["email"])

        for key, value in updates.items():
            setattr(customer, key, value)

        db.commit()
        db.refresh(customer)
        return customer

    @staticmethod
    def deactivate_customer(db: Session, customer_id: int):
        customer = CustomerService.get_customer(db, customer_id)
        customer.is_active = False
        db.commit()

        return {"message": "Customer deactivated successfully"}