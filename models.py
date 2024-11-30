from datetime import datetime
from bson import ObjectId


# Users Collection
class Users:
    COLLECTION_NAME = "users"

    @staticmethod
    def create_user(mongo, name, email, password, phone):
        user = {
            "name": name,
            "email": email,
            "password": password,  # Hashed password
            "phone": phone,
            "payment_method": None,  # Reference to PaymentMethod
            "transactions": []  # List of Transaction IDs
        }
        return mongo.db[Users.COLLECTION_NAME].insert_one(user).inserted_id

    @staticmethod
    def get_user_by_email(mongo, email):
        return mongo.db[Users.COLLECTION_NAME].find_one({"email": email})

    @staticmethod
    def get_user_by_id(mongo, user_id):
        return mongo.db[Users.COLLECTION_NAME].find_one({"_id": ObjectId(user_id)})


# PaymentMethod Collection
class PaymentMethod:
    COLLECTION_NAME = "paymentMethods"

    @staticmethod
    def add_payment_method(mongo, user_id, card_number, cvv, card_type, expire_date, bill_addr, card_holder_name):
        payment_method = {
            "user_id": ObjectId(user_id),
            "CardNumber": card_number,
            "CVV": cvv,
            "CardType": card_type,
            "ExpireDate": expire_date,
            "BillAddr": bill_addr,
            "CardHolderName": card_holder_name
        }
        return mongo.db[PaymentMethod.COLLECTION_NAME].insert_one(payment_method).inserted_id

    @staticmethod
    def get_payment_method(mongo, user_id):
        return mongo.db[PaymentMethod.COLLECTION_NAME].find_one({"user_id": ObjectId(user_id)})


# Location Collection
class Location:
    COLLECTION_NAME = "locations"

    @staticmethod
    def add_location(mongo, location_id, venue_name, address, country, state, postal_code):
        location = {
            "_id": location_id,
            "venue_name": venue_name,
            "address": address,
            "country": country,
            "state": state,
            "postal_code": postal_code,
            "events": [],  # List of Event IDs
            "images": []  # List of Image IDs
        }
        return mongo.db[Location.COLLECTION_NAME].insert_one(location).inserted_id

    @staticmethod
    def get_location_by_id(mongo, location_id):
        return mongo.db[Location.COLLECTION_NAME].find_one({"_id": location_id})


# Event Collection
class Event:
    COLLECTION_NAME = "events"

    @staticmethod
    def add_event(mongo, event_id, event_name, event_date, event_type, location_id):
        event = {
            "_id": event_id,
            "event_name": event_name,
            "event_date": event_date,
            "event_type": event_type,
            "location_id": location_id,
            "ticket_categories": [],  # List of TicketCategory IDs
            "queue": [],  # List of Queue IDs
            "images": []  # List of Image IDs
        }
        return mongo.db[Event.COLLECTION_NAME].insert_one(event).inserted_id

    @staticmethod
    def get_event_by_id(mongo, event_id):
        return mongo.db[Event.COLLECTION_NAME].find_one({"_id": event_id})


# Ticket Collection
class Ticket:
    COLLECTION_NAME = "tickets"

    @staticmethod
    def add_ticket(mongo, category_id, event_id, seat_no, status, transaction_id):
        ticket = {
            "CatID": category_id,
            "EventID": event_id,
            "SeatNo": seat_no,
            "Status": status,
            "TranscID": transaction_id
        }
        return mongo.db[Ticket.COLLECTION_NAME].insert_one(ticket).inserted_id

    @staticmethod
    def get_tickets_by_event_id(mongo, event_id):
        return mongo.db[Ticket.COLLECTION_NAME].find({"event_id": event_id})


# TicketCategory Collection
class TicketCategory:
    COLLECTION_NAME = "ticket_categories"

    @staticmethod
    def add_category(mongo, category_id, event_id, category_name, price, seats_available):
        category = {
            "_id": category_id,
            "event_id": event_id,
            "category_name": category_name,
            "price": price,
            "seats_available": seats_available,
            "tickets": []  # List of Ticket IDs
        }
        return mongo.db[TicketCategory.COLLECTION_NAME].insert_one(category).inserted_id

    @staticmethod
    def get_category_by_id(mongo, category_id):
        return mongo.db[TicketCategory.COLLECTION_NAME].find_one({"_id": category_id})


# Transactions Collection
class Transactions:
    COLLECTION_NAME = "transactions"

    @staticmethod
    def add_transaction(mongo, user_id, amount, status, card_id, event_id):
        transaction = {
            "TranAmount": float(amount),     
            "TransDate": datetime.utcnow(),       
            "TranStatus": status,
            "UserID": ObjectId(user_id), 
            "UserID": ObjectId(user_id), 
            "EventID": ObjectId(event_id),
            "CardID": ObjectId(card_id)
        }
        return mongo.db[Transactions.COLLECTION_NAME].insert_one(transaction).inserted_id

    @staticmethod
    def get_transactions_by_user(mongo, user_id):
        return mongo.db[Transactions.COLLECTION_NAME].find({"user_id": ObjectId(user_id)})


# Queue Collection
class Queue:
    COLLECTION_NAME = "queues"

    @staticmethod
    def join_queue(mongo, user_id, event_id):
        queue = {
            "user_id": ObjectId(user_id),
            "event_id": event_id,
            "created_at": datetime.utcnow()
        }
        return mongo.db[Queue.COLLECTION_NAME].insert_one(queue).inserted_id

    @staticmethod
    def get_queue_for_event(mongo, event_id):
        return mongo.db[Queue.COLLECTION_NAME].find({"event_id": event_id}).sort("created_at", 1)


# Image Collection
class Image:
    COLLECTION_NAME = "images"

    @staticmethod
    def add_image(mongo, url, ratio, width, height, event_id=None, location_id=None):
        image = {
            "url": url,
            "ratio": ratio,
            "width": width,
            "height": height,
            "event_id": event_id,
            "location_id": location_id
        }
        return mongo.db[Image.COLLECTION_NAME].insert_one(image).inserted_id

    @staticmethod
    def get_images_for_event(mongo, event_id):
        return mongo.db[Image.COLLECTION_NAME].find({"event_id": event_id})

    @staticmethod
    def get_images_for_location(mongo, location_id):
        return mongo.db[Image.COLLECTION_NAME].find({"location_id": location_id})