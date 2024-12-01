from flask import Flask, render_template, request, session, url_for, redirect, jsonify, make_response, flash
from config import Config
# from sqlalchemy import text, extract,and_, func
# from sqlalchemy.orm import joinedload
# from sqlalchemy.sql import exists
# from sqlalchemy.exc import IntegrityError
from flask_pymongo import PyMongo
from pymongo import ASCENDING
from api.ticketmaster import fetch_and_store_events
import logging, traceback
from datetime import datetime
import calendar
import bcrypt
from auth import hash_password, verify_password, RegistrationForm, LoginForm
import calendar
from collections import defaultdict
from models import Users, PaymentMethod, Location, Event, Ticket, TicketCategory, Transactions, Image, Queue
from werkzeug.security import generate_password_hash 
from bson.objectid import ObjectId
import math

app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = Config.APP_SECRET_KEY
# app.config['WTF_CSRF_ENABLED'] = False
app.config["MONGO_URI"] = Config.MONGO_URI

mongo = PyMongo(app)

API_KEY = Config.TICKETMASTER_API_KEY

with app.app_context():
    events_to_fetch = 0

    if not hasattr(mongo, 'db'):
        print("MongoDB not properly initialized.")
    else:
        try:
            # Access the collection
            events_collection = mongo.db.events

            # Get the current count of events in the database
            current_event_count = events_collection.count_documents({})

            print(f"Currently {current_event_count} events in the database.")

            if current_event_count < events_to_fetch:
                needed_events = events_to_fetch - current_event_count
                print(f"Less than {events_to_fetch} events found in the database, fetching more events...")
                fetch_and_store_events(API_KEY, needed_events, mongo)
            else:
                print("Sufficient events are already stored in the database, no action needed.")

        except Exception as e:
            # Adjust the error handling to log appropriate MongoDB errors if necessary
            print(f"An error occurred: {str(e)}\n{traceback.format_exc()}")
    
# Routes
@app.route('/')
def home():
    preferred_width = 1920

    hot_events_info = list(mongo.db.events.aggregate([
        {
            '$lookup': {
                'from': 'transactions',
                'localField': '_id',
                'foreignField': 'event_id',
                'as': 'transactions'
            }
        },
        {
            '$addFields': {
                'transaction_count': {'$size': '$transactions'}
            }
        },
        {
            '$sort': {'transaction_count': -1}
        },
        {'$limit': 6}
    ]))

    # Check if enough events were fetched
    if len(hot_events_info) < 6:
        additional_events_needed = 6 - len(hot_events_info)
        more_events = list(mongo.db.events.find().limit(additional_events_needed))
        hot_events_info.extend(more_events)

    # Prepare data to render
    hot_events = []
    for event in hot_events_info:
        # Fetch the image for each event
        image = mongo.db.images.find_one({'EventID': event.get('id')}) or {}
        image_url = image['URL'] if image else 'static/images/event1.jpg'

        hot_events.append({
            'id': str(event['id']),
            'name': event['name'],
            'ImageURL': image_url
        })

    # Fetch the most popular venues based on the number of associated events
    top_venues = list(mongo.db.locations.aggregate([
        {
            '$lookup': {
                'from': 'events',
                'localField': '_id',
                'foreignField': 'location_id',
                'as': 'events'
            }
        },
        {
            '$addFields': {
                'event_count': {'$size': '$events'}
            }
        },
        {'$sort': {'event_count': -1}},
        {'$limit': 6}
    ]))

    formatted_venues = []
    for venue in top_venues:
        image_url = venue.get('image_url', url_for('static', filename='images/default.jpg'))

        # Fetch the image for each venue
        image = mongo.db.images.find_one({'LocationID': venue.get('id')}) or {}
        image_url = image['URL'] if image else 'static/images/venue1.jpg'

        formatted_venues.append({
            'LocationID': str(venue['_id']),
            'VenueName': venue['name'],
            'ImageURL': image_url
        })

    return render_template('landing.html', hot_events=hot_events, venues=formatted_venues)

@app.route('/event')
def event():
    search_query = request.args.get('search', '')
    search_month = request.args.get('month', '')
    current_page = int(request.args.get('page', 1))  # Default to page 1 if not provided

    # MongoDB query setup
    query = {}
    if search_query:
        query = {
            "$or": [
                {"name": {"$regex": search_query, "$options": "i"}},
                {"description": {"$regex": search_query, "$options": "i"}}
            ]
        }

    # Calculate total pages and current pagination
    total_count = mongo.db.events.count_documents(query)
    items_per_page = 10
    total_pages = math.ceil(total_count / items_per_page)

    # Fetching events from MongoDB
    events = mongo.db.events.find(query).sort("startDateTime", ASCENDING).skip(
        (current_page - 1) * items_per_page).limit(items_per_page)

    # Group events by month and year
    events_by_month_year = {}
    for event in events:
        event_date = event.get('startDateTime')
        if event_date:
            month_year = event_date.strftime('%B %Y')
            if month_year not in events_by_month_year:
                events_by_month_year[month_year] = []
         
            # Fetch the image for each event
            image = mongo.db.images.find_one({'EventID': event.get('id')}) or {}
            event['preferred_image'] = image.get('URL')

            events_by_month_year[month_year].append(event)

    return render_template(
        'event.html',
        events_by_month_year=events_by_month_year,
        search_query=search_query,
        search_month=search_month,
        current_page=current_page,
        total_pages=total_pages
    )

@app.route('/venue')
def venue():
    preferred_width = 1920
    page = request.args.get('page', 1, type=int)
    per_page = 12

    search_query = request.args.get('search', '').strip()

    # Access the MongoDB collections
    locations_collection = mongo.db.locations
    images_collection = mongo.db.images

    # MongoDB filter for search query
    filter_query = {}
    if search_query:
        filter_query['name'] = {'$regex': search_query, '$options': 'i'}

    # Fetch the locations with pagination
    locations_cursor = locations_collection.find(filter_query).skip((page - 1) * per_page).limit(per_page)
    locations = list(locations_cursor)

    # Fetch images for the locations
    venue_with_images = []
    for location in locations:
        # Fetch the image for the location
        image = images_collection.find_one({'LocationID': location.get('id')}) or {}
        image_url = image['URL'] if image else 'static/images/venue1.jpg'

        venue = {
            'LocationID': location.get('id'),
            'VenueName': location.get('name', 'Unknown Venue'),
            'Address': location.get('address', 'No Address Provided'),
            'Country': location.get('country', 'Unknown Country'),
            'State': location.get('state', 'Unknown State'),
            'PostalCode': location.get('postalCode', 'Unknown Postal Code'),
            'ImageURL': image_url
        }
        venue_with_images.append(venue)

    # Count total venues for pagination
    total_venues = locations_collection.count_documents(filter_query)
    total_pages = (total_venues + per_page - 1) // per_page

    # Pagination metadata
    pagination = {
        'page': page,
        'total_pages': total_pages,
        'has_next': page < total_pages,
        'has_prev': page > 1,
        'next_num': page + 1 if page < total_pages else None,
        'prev_num': page - 1 if page > 1 else None,
    }
    return render_template('venue.html', venues=venue_with_images, pagination=pagination, search_query=search_query)

@app.route('/venueinfo/<LocationID>')
def venueinfo(LocationID):
    # Access the MongoDB collections
    locations_collection = mongo.db.locations
    images_collection = mongo.db.images

    # Fetch the venue details by LocationID
    venue = locations_collection.find_one({'id': LocationID})
    if not venue:
        return "Venue not found", 404

    # Fetch the associated image, if available
    image = images_collection.find_one({'LocationID': LocationID})
    image_url = image['URL'] if image else 'static/images/venue1.jpg'  # Default image if none found

    # Prepare the venue details for rendering
    venue_details = {
        'LocationID': venue.get('id', 'Unknown LocationID'),
        'VenueName': venue.get('name', 'Unknown Venue'),
        'Address': venue.get('address', 'No Address Provided'),
        'Country': venue.get('country', 'Unknown Country'),
        'State': venue.get('state', 'Unknown State'),
        'PostalCode': venue.get('postalCode', 'Unknown Postal Code')
    }

    return render_template('venueinfo.html', venue=venue_details, image_url=image_url)

@app.route('/registersignup')
def registersignup():
    return render_template('registersignup.html', registration_form=RegistrationForm(), login_form=LoginForm())

@app.route('/register', methods=['POST'])
def register():
    registration_form = RegistrationForm(request.form)
    login_form = LoginForm()  # Ensure login_form is initialized
    error_message = None  # Initialize error_message

    if registration_form.validate_on_submit():
        email = registration_form.email.data

        # Check if email already exists in the database
        existing_user = mongo.db.users.find_one({'Email': email})
        
        if existing_user:
            # If email exists, return an error message
            error_message = 'Email already registered.'
            return render_template('registersignup.html', registration_form=registration_form, login_form=login_form, error_message=error_message)

        # If email doesn't exist, proceed to create new user
        hashed_password = bcrypt.hashpw(registration_form.password.data.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        new_user = {
            'Name': registration_form.name.data,
            'Email': email,
            'Password': hashed_password,
            'Phone': registration_form.phone.data
        }

        try:
            # Insert the new user into the MongoDB collection
            mongo.db.users.insert_one(new_user)
        except Exception as e:
            # Log the exception and return an error message
            logging.error(f"An error occurred while inserting a new user: {e}")
            error_message = 'A database error occurred. Please try again.'
            return render_template('registersignup.html', registration_form=registration_form, login_form=login_form, error_message=error_message)

        # Success message
        error_message = 'You have successfully registered!'
        return render_template('landing.html', error_message=error_message)

    else:
        # Log form errors for debugging
        logging.error(f"Form Errors: {registration_form.errors}")

        # Generate error messages for the form fields
        error_message = ""
        for field, errors in registration_form.errors.items():
            for error in errors:
                error_message += f"Error in the {getattr(registration_form, field).label.text} field - {error}. "

    # Return form with error messages if validation fails
    return render_template('registersignup.html', registration_form=registration_form, login_form=login_form, error_message=error_message)

@app.route('/login', methods=['POST'])
def login():
    login_form = LoginForm(request.form)
    registration_form = RegistrationForm()

    email = login_form.email.data
    password = login_form.password.data
    
    if login_form.validate_on_submit():
        # Query the MongoDB collection for the user
        user = mongo.db.users.find_one({'Email': email})

        if user and bcrypt.checkpw(password.encode('utf-8'), user['Password'].encode('utf-8')):
            # Save the user ID in the session
            session['user_id'] = str(user['_id'])  # Use the MongoDB ObjectId as the session ID
            error_message = 'Login successful!'
            # return render_template('landing.html', error_message=error_message)
            return redirect(url_for('home'))
        else:
            error_message = 'Invalid email or password'
            return render_template('registersignup.html', login_form=login_form, registration_form=registration_form, error_message=error_message)
    else:
        logging.error(f"Form Errors: {login_form.errors}")
        error_message = ""
        for fieldName, errorMessages in login_form.errors.items():
            for err in errorMessages:
                error_message += f'{fieldName}: {err} '

    return render_template('registersignup.html', login_form=login_form, registration_form=registration_form, error_message=error_message)

@app.route('/logout')
def logout():
    session.pop('user_id', None)  # Remove user_id from session
    error_message = 'You have been logged out.'
    # return render_template('landing.html', error_message=error_message)
    return redirect(url_for('home'))

@app.route('/myticket')
def myticket():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('registersignup'))

    # Use MongoDB queries to retrieve ticket and transaction details
    try:
        # Query the transactions for the current user
        transactions = list(mongo.db.transactions.find({"UserID": user_id}).sort("TransDate", -1))

        ticket_details = []
        transaction_details = []

        for transaction in transactions:
            tickets = list(mongo.db.tickets.find({"TranscID": str(transaction["_id"])}))
            for ticket in tickets:
                event = mongo.db.events.find_one({"id": ticket["EventID"]})
                category = mongo.db.ticketCategories.find_one({"_id": ticket["CatID"]})

                # Ticket details for upcoming and finished events, comparing event date
                ticket_info = {
                    'TranscID': transaction["_id"],
                    'TransDate': transaction["TransDate"].strftime('%d-%m-%Y %I:%M%p'),
                    'EventName': event["name"] if event else "Unknown Event",
                    'SeatNo': ticket["SeatNo"],
                    'SeatCategory': category["CatName"] if category else "Unknown Category",
                    'Status': 'upcoming' if event and event["startDateTime"] > datetime.now() else 'finished'
                }
                ticket_details.append(ticket_info)

                # Transaction details without seat category (just amount and status)
                transaction_info = {
                    'TranscID': str(transaction["_id"]),
                    'TransDate': transaction["TransDate"].strftime('%d-%m-%Y %I:%M%p'),
                    'EventName': event["name"] if event else "Unknown Event",
                    'SeatNo': ticket["SeatNo"],
                    'Amount': transaction["TranAmount"],
                    'Status': transaction["TranStatus"]
                }
                transaction_details.append(transaction_info)

        return render_template('myticket.html', ticket_details=ticket_details, transaction_details=transaction_details)

    except Exception as e:
        logging.error(f"An error occurred while retrieving tickets: {e}\n{traceback.format_exc()}")
        return "An error occurred. Please try again later.", 500

@app.route('/ticket/<event_id>')
def ticket(event_id):
    error_message = None

    # Get user info from session
    user_id = session.get('user_id')    
    # Fetch the user from the MongoDB `users` collection
    object_id = ObjectId(user_id)
    user = mongo.db.users.find_one({"_id": object_id})
    if user:
        # Convert ObjectId to string here
        user['_id'] = str(user['_id'])
    if not user:
        error_message = 'Please log in first'
        return render_template(
            'registersignup.html',
            error_message=error_message,
            registration_form=RegistrationForm(),
            login_form=LoginForm()
        )

    # Fetch event details
    event = mongo.db.events.find_one({"id": event_id})
    if not event:
        return redirect(url_for('landing'))

    # Fetch ticket categories for the event
    ticket_categories = list(mongo.db.ticketCategories.find({"EventID": event_id}))

    # Determine ticket availability
    tickets_available = True  # Assume tickets are available if no documents are found
    if mongo.db.tickets.count_documents({}) > 0:  # Checks if the tickets collection has any documents
        tickets_available = any(
            category["SeatsAvailable"] > mongo.db.tickets.count_documents({"CatID": str(category["_id"])})
            for category in ticket_categories
        )

    image = mongo.db.images.find_one({'EventID': event.get('id')}) or {}
    event_image = image['URL'] if image else 'static/images/event1.jpg'

    # Fetch payment method for the user
    payment_method = mongo.db.paymentMethods.find_one({"user_id": user_id})
    if payment_method and 'ExpireDate' in payment_method:
        payment_method['ExpireDate'] = datetime.strptime(payment_method['ExpireDate'], '%Y-%m-%d')

    # Render the ticket page
    return render_template(
        'ticket.html',
        event=event,
        ticket_categories=ticket_categories,
        calendar=calendar,
        event_image=event_image,
        user=user,
        tickets_available=tickets_available,
        payment_method=payment_method
    )

@app.route('/ticket_purchase/<event_id>', methods=['POST'])
def ticket_purchase(event_id):
    # Check if the user is logged in
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('registersignup'))

    # Check if the event exists
    event = mongo.db.events.find_one({"id": event_id})
    if not event:
        return redirect(url_for('login'))

    # Get ticket categories for the event
    ticket_categories = list(mongo.db.ticketCategories.find({"EventID": event_id}))
    if not ticket_categories:
        return redirect(url_for('ticket', event_id=event_id))

    try:
        category_id = request.form.get('category')
        quantity = int(request.form.get('quantity'))

        # Fetch the selected ticket categoryticket_categorie
        object_id = ObjectId(category_id)
        ticket_category = mongo.db.ticketCategories.find_one({"_id": object_id})
        if not ticket_category:
            # flash('Ticket category not found.', 'error')
            return redirect(url_for('ticket', event_id=event_id))

        # Check if there are enough seats available
        if ticket_category['SeatsAvailable'] < quantity:
            # flash('Not enough tickets available', 'error')
            return redirect(url_for('ticket', event_id=event_id))

        # Collect payment information
        cardholder_name = request.form.get('cardholder-name')
        card_number = request.form.get('card-number')
        cvv = request.form.get('cvv')
        expiry_month = request.form.get('expiry-month')
        expiry_year = request.form.get('expiry-year')
        billing_address = request.form.get('billing-address')

        # Check if the user has an existing payment method
        payment_method = mongo.db.paymentMethods.find_one({"user_id": user_id})
        if payment_method:
            # Update existing payment method
            object_id = ObjectId(user_id)
            mongo.db.paymentMethods.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "CVV": cvv,
                        "ExpireDate": f"{expiry_year}-{expiry_month}-01",
                        "BillAddr": billing_address,
                        "CardHolderName": cardholder_name,
                    }
                },
            )
        else:
            # Create a new payment method
            payment_method = {
                "user_id": user_id,
                "CardNumber": card_number,
                "CVV": cvv,
                "CardType": "Unknown",
                "ExpireDate": f"{expiry_year}-{expiry_month}-01",
                "BillAddr": billing_address,
                "CardHolderName": cardholder_name,
            }
            mongo.db.paymentMethods.insert_one(payment_method)

        # Create transaction
        total_price = ticket_category["CatPrice"] * quantity
        transaction = {
            "TranAmount": total_price,
            "TransDate": datetime.utcnow(),
            "TranStatus": "Completed",
            "UserID": user_id,
            "EventID": event_id,
            "CardID": payment_method.get('_id')
        }
        transaction_id = mongo.db.transactions.insert_one(transaction).inserted_id

        # Allocate tickets
        tickets = []
        highest_seat_cursor = mongo.db.tickets.find({"CatID": category_id}).sort("SeatNo", -1).limit(1)
        highest_seat = 0
        try:
            highest_seat_document = highest_seat_cursor.next()
            highest_seat = highest_seat_document["SeatNo"]
        except StopIteration:
            highest_seat = 0  # No tickets found, assume starting at seat 0

        if highest_seat + quantity > ticket_category["SeatsAvailable"]:
            # flash('Not enough tickets available', 'error')
            return redirect(url_for('ticket', event_id=event_id))

        start_seat_number = highest_seat + 1
        for i in range(quantity):
            seat_number = start_seat_number + i
            ticket = {
                "CatID": category_id,
                "EventID": event_id,
                "SeatNo": seat_number,
                "Status": "Issued",
                "TranscID": str(transaction_id),
            }
            tickets.append(ticket)

        mongo.db.tickets.insert_many(tickets)

        # Update the seats available for the ticket category
        mongo.db.ticketCategories.update_one(
            {"CatID": category_id}, {"$inc": {"SeatsAvailable": -quantity}}
        )

        # flash('Purchase successful!', 'success')
        return redirect(url_for('myticket'))

    except Exception as e:
        logging.error(f"Error during ticket purchase: {e}\n{traceback.format_exc()}")
        # flash('An error occurred during the purchase. Please try again.', 'error')
        return redirect(url_for('ticket', event_id=event_id))


@app.route('/queue/<event_id>')
def queue(event_id):
    # Check if the user is logged in
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('registersignup'))

    preferred_width = 1920

    # Fetch the event from MongoDB
    event = mongo.db.events.find_one({"id": event_id})
    if not event:
        return redirect(url_for('login'))

    # Choose preferred image based on width
    if "images" in event and event["images"]:
        preferred_image = min(
            event["images"], key=lambda img: abs(img.get("Width", preferred_width) - preferred_width)
        )
        image_url = preferred_image["URL"]
    else:
        image_url = url_for('static', filename='images/default.jpg')

    # Prepare event information for the template
    event_image = {
        "ImageURL": image_url,
    }

    return render_template('enterqueue.html', event_image=event_image, event=event)


@app.route('/joinqueue/<event_id>', methods=['POST'])
def joinqueue(event_id):
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('registersignup'))

    # Fetch the user from the MongoDB `users` collection
    object_id = ObjectId(user_id)
    user = mongo.db.users.find_one({"_id": object_id})
    if user:
        # Convert ObjectId to string here
        user['_id'] = str(user['_id'])
    if not user:
        error_message = 'Please log in first'
        return render_template(
            'registersignup.html',
            error_message=error_message,
            registration_form=RegistrationForm(),
            login_form=LoginForm()
        )

    # Fetch event from MongoDB
    event = mongo.db.events.find_one({"id": event_id})
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('landing'))

    # Check if the user is already in the queue for this event
    existing_queue = mongo.db.queues.find_one({"user_id": user_id, "event_id": event_id})
    if existing_queue:
        flash('You are already in the queue for this event.', 'info')
        return redirect(url_for('event', event_id=event_id))

    # Choose preferred image based on width
    preferred_width = 1920
    if "images" in event and event["images"]:
        preferred_image = min(
            event["images"], key=lambda img: abs(img.get("Width", preferred_width) - preferred_width)
        )
        image_url = preferred_image["URL"]
    else:
        image_url = url_for('static', filename='images/default.jpg')

    event_image = {
        "ImageURL": image_url,
    }

    try:
        # Insert new queue entry into MongoDB
        queue_entry = {
            "user_id": user_id,
            "event_id": event_id,
            "timestamp": datetime.now()
        }
        result = mongo.db.queues.insert_one(queue_entry)

        # Fetch queue number (using the document's auto-generated `_id` field)
        queue_no = result.inserted_id

        data = {
            "UserID": user_id,
            "EventID": event_id,
            "QNo": str(queue_no)
        }

    except Exception as e:
        flash('Failed to join the queue. Please try again.', 'error')
        logging.error(f"Error during queue addition: {e}\n{traceback.format_exc()}")
        return redirect(url_for('event', event_id=event_id))

    return render_template('queue.html', data=data, event_image=event_image, event=event)

@app.route('/joinqueue/<event_id>/inqueue/<queue_id>')
def inqueue(event_id, queue_id):
    user_id = session.get('user_id')

    # Fetch the event from MongoDB
    event = mongo.db.events.find_one({"id": event_id})
    if not event:
        return redirect(url_for('landing'))

    # Preferred image selection
    preferred_width = 1920
    if "images" in event and event["images"]:
        preferred_image = min(
            event["images"], key=lambda img: abs(img.get("Width", preferred_width) - preferred_width)
        )
        image_url = preferred_image["URL"]
    else:
        image_url = url_for('static', filename='images/default.jpg')

    event_image = {
        "ImageURL": image_url,
    }

    # Retrieve the top user in the queue for the event
    top_queue_entry = mongo.db.queues.find_one({"event_id": event_id}, sort=[("timestamp", 1)])

    if top_queue_entry:
        top_user_id = top_queue_entry["user_id"]

        # Check if the logged-in user is at the top of the queue
        if top_user_id == user_id:
            # Fetch the user from the MongoDB `users` collection
            object_id = ObjectId(user_id)
            user = mongo.db.users.find_one({"_id": object_id})
            if user:
                # Convert ObjectId to string here
                user['_id'] = str(user['_id'])

            # Determine ticket availability
            tickets_available = any(
                category["SeatsAvailable"] > mongo.db.tickets.count_documents({"CatID": category["CatID"]})
                for category in event.get("ticketCategories", [])
            )

            # Optionally remove the user from the queue after they proceed
            mongo.db.queues.delete_one({"_id": top_queue_entry["_id"]})  # Remove the queue entry

            return render_template(
                'ticket.html',
                event=event,
                calendar=calendar,
                event_image=event_image,
                user=user,
                tickets_available=tickets_available,
                payment_method=mongo.db.paymentMethods.find_one({"user_id": user_id}),
            )

        else:
            # The current user is not the top user, just re-render the queue page
            data = {
                "UserID": user_id,
                "EventID": event_id,
                "QNo": queue_id,
            }
            return render_template("queue.html", data=data, event_image=event_image, event=event)

    else:
        return redirect(url_for("queue", event_id=event_id))

@app.context_processor
def inject_user():
    user_id = session.get('user_id')
    user = None
    if user_id and isinstance(user_id, str) and len(user_id) == 24:
        try:
            object_id = ObjectId(user_id)  # Ensure user_id is a valid ObjectId string
            user = mongo.db.users.find_one({"_id": object_id})
            if user:
                user['_id'] = str(user['_id'])
        except bson.errors.InvalidId:
            # Log invalid user_id attempts or handle them as needed
            app.logger.error("Invalid user_id in session")
    return dict(user=user)

@app.route('/aboutus', methods=['GET', 'POST'])
def aboutus():
    # Query for most popular event
    most_popular_event = mongo.db.tickets.aggregate([
        {
            "$lookup": {
                "from": "events",
                "localField": "EventID",
                "foreignField": "id",
                "as": "event"
            }
        },
        {"$unwind": "$event"},
        {
            "$group": {
                "_id": "$event.name",
                "TicketsSold": {"$sum": 1}
            }
        },
        {"$sort": {"TicketsSold": -1}},
        {"$limit": 1}
    ])
    most_popular_event = list(most_popular_event)
    most_popular_event = most_popular_event[0] if most_popular_event else None

    # Query for total tickets sold
    total_tickets_sold = mongo.db.tickets.count_documents({})

    # Query for total unique events and locations
    total_events = mongo.db.events.count_documents({})
    total_locations = mongo.db.locations.count_documents({})

    # Query for ticket categories (Pie Chart)
    category_results = mongo.db.ticketCategories.aggregate([
        {
            "$group": {
                "_id": "$CatName",
                "TotalTickets": {"$sum": 1}
            }
        }
    ])
    category_results = list(category_results)
    category_names = [result["_id"] for result in category_results]
    category_data = [result["TotalTickets"] for result in category_results]

    # Query for events by location (Doughnut Chart)
    location_results = mongo.db.events.aggregate([
        {"$unwind": "$venues"},  # Unwind the venues array first to access the objects within
        {
            "$group": {
                "_id": "$venues.name",  # Group by venue name directly from the unwound venues
                "EventsCount": {"$sum": 1}  # Count the number of events per venue
            }
        },
        {"$sort": {"EventsCount": -1}}  # Optional: sort by the count of events descending
    ])
    location_results = list(location_results)
    location_names = [result["_id"] for result in location_results]
    location_data = [result["EventsCount"] for result in location_results]

    # Query for ticket sales data (Line Chart)
    ticket_sales_results = mongo.db.transactions.aggregate([
        {
            "$addFields": {
                "trans_id_str": {"$toString": "$_id"}  # Convert ObjectId to string
            }
        },
        {
            "$lookup": {
                "from": "tickets",
                "localField": "trans_id_str",  # Use the string version for lookup
                "foreignField": "TranscID",
                "as": "ticket_info"
            }
        },
        {"$unwind": "$ticket_info"},  # Unwind the results from lookup
        {
            "$group": {
                "_id": {
                    "year": {"$year": "$TransDate"},
                    "month": {"$month": "$TransDate"},
                    "day": {"$dayOfMonth": "$TransDate"}
                },
                "TicketsSold": {"$sum": 1}
            }
        },
        {"$sort": {"_id": 1}}
    ])
    ticket_sales_results = list(ticket_sales_results)
    ticket_sales_dates = [
        f"{result['_id']['year']}-{result['_id']['month']:02d}-{result['_id']['day']:02d}"
        for result in ticket_sales_results if result['_id']  # Ensure there's a result to format
    ]
    ticket_sales_data = [result["TicketsSold"] for result in ticket_sales_results]

    # Query for revenue data (Bar Chart)
    revenue_results = mongo.db.tickets.aggregate([
        {
            "$lookup": {
                "from": "events",
                "localField": "EventID",
                "foreignField": "id",
                "as": "event"
            }
        },
        {"$unwind": "$event"},
        {
            "$lookup": {
                "from": "ticketCategories",
                "let": {"category_id": {"$toObjectId": "$CatID"}},  # Convert CatID from string to ObjectId
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$_id", "$$category_id"]}}}
                ],
                "as": "category"
            }
        },
        {"$unwind": "$category"},
        {
            "$group": {
                "_id": "$event.name",
                "TotalRevenue": {"$sum": "$category.CatPrice"}
            }
        },
        {"$sort": {"TotalRevenue": -1}}  # Optional: sort by the total revenue descending
    ])
    revenue_results = list(revenue_results)
    revenue_event_names = [result["_id"] for result in revenue_results]
    revenue_data = [result["TotalRevenue"] for result in revenue_results]

    # Query for revenue per event type
    revenue_per_event_type_results = mongo.db.events.aggregate([
        {
            "$lookup": {
                "from": "tickets",
                "localField": "id",
                "foreignField": "EventID",
                "as": "tickets"
            }
        },
        {"$unwind": "$tickets"},
        {
            "$lookup": {
                "from": "ticketCategories",
                "let": {"cat_id": {"$toObjectId": "$tickets.CatID"}},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$_id", "$$cat_id"]}}}
                ],
                "as": "ticketCategory"
            }
        },
        {"$unwind": "$ticketCategory"},
        {
            "$group": {
                "_id": "$type",
                "TotalRevenue": {"$sum": "$ticketCategory.CatPrice"}
            }
        },
        {"$sort": {"TotalRevenue": -1}}
    ])
    revenue_per_event_type_results = list(revenue_per_event_type_results)
    event_types = [result["_id"] for result in revenue_per_event_type_results]
    revenues = [result["TotalRevenue"] for result in revenue_per_event_type_results]

    # Query to get average daily sales per hour
    purchase_times_results = mongo.db.transactions.aggregate([
        {
            "$group": {
                "_id": {"hour": {"$hour": "$TransDate"}},
                "avg_sales": {"$avg": 1}
            }
        },
        {"$sort": {"_id.hour": 1}}
    ])
    purchase_times_results = list(purchase_times_results)
    purchase_hours = [f"{int(result['_id']['hour']):02}:00" for result in purchase_times_results]
    average_sales = [result["avg_sales"] for result in purchase_times_results]

    # Query for all event names (for the search dropdown)
    event_list = mongo.db.events.distinct("name")

    # Query for all categories (for the filter dropdown)
    category_list = mongo.db.ticketCategories.distinct("CatName")

    # Query for ticket sales data for the default event
    default_event = 'Phoenix Suns vs. Portland Trail Blazers'
    ticket_sales_results_default = mongo.db.tickets.aggregate([
        {
            "$lookup": {
                "from": "events",
                "localField": "EventID",
                "foreignField": "id",
                "as": "event"
            }
        },
        {"$unwind": "$event"},
        {"$match": {"event.name": {"$regex": default_event, "$options": "i"}}},
        {
            "$group": {
                "_id": "$event.startDateTime",
                "TicketsSold": {"$sum": 1}
            }
        },
        {"$sort": {"_id": 1}}
    ])
    ticket_sales_results_default = list(ticket_sales_results_default)
    ticket_sales_dates_default = [result["_id"].strftime('%Y-%m-%d') for result in ticket_sales_results_default]
    ticket_sales_data_default = [result["TicketsSold"] for result in ticket_sales_results_default]

    # Query revenue data for the default event
    revenue_results_default = mongo.db.tickets.aggregate([
        {
            "$lookup": {
                "from": "events",
                "localField": "EventID",
                "foreignField": "id",
                "as": "event"
            }
        },
        {"$unwind": "$event"},
        {"$match": {"event.name": {"$regex": default_event, "$options": "i"}}},
        {
            "$group": {
                "_id": "$event.name",
                "TotalRevenue": {"$sum": "$price"}
            }
        }
    ])
    revenue_results_default = list(revenue_results_default)
    revenue_event_names_default = [result["_id"] for result in revenue_results_default]
    revenue_data_default = [result["TotalRevenue"] for result in revenue_results_default]

    return render_template('aboutus.html',
                           event_types=event_types,
                           revenues=revenues,
                           purchase_hours=purchase_hours,
                           average_sales=average_sales,
                           most_popular_event=most_popular_event,
                           total_tickets_sold=total_tickets_sold,
                           total_events_locations={"TotalEvents": total_events, "TotalLocations": total_locations},
                           ticket_sales_data=ticket_sales_data,
                           ticket_sales_dates=ticket_sales_dates,
                           revenue_data=revenue_data,
                           revenue_event_names=revenue_event_names,
                           category_data=category_data,
                           category_names=category_names,
                           location_names=location_names,
                           location_data=location_data,
                           event_list=event_list,
                           category_list=category_list,
                           ticket_sales_data_default=ticket_sales_data_default,
                           ticket_sales_dates_default=ticket_sales_dates_default,
                           revenue_data_default=revenue_data_default,
                           revenue_event_names_default=revenue_event_names_default,
                           default_event=default_event)


@app.route('/get_event_data', methods=['POST'])
def get_event_data():
    event_name = request.json.get('event_name').lower()

    # Query ticket sales for the selected event using transaction date and event name
    ticket_sales_results = mongo.db.tickets.aggregate([
        {
            "$lookup": {
                "from": "transactions",
                "let": {"transc_id": "$TranscID"},  # Use TranscID directly as a string
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$eq": [{"$toString": "$_id"}, "$$transc_id"]  # Convert _id to string for comparison
                            }
                        }
                    },
                    {
                        "$lookup": {
                            "from": "events",
                            "let": {"event_id": "$EventID"},
                            "pipeline": [
                                {
                                    "$match": {
                                        "$expr": {
                                            "$eq": ["$id", "$$event_id"]
                                        }
                                    }
                                }
                            ],
                            "as": "event_details"
                        }
                    },
                    {"$unwind": "$event_details"},
                    {"$match": {"event_details.name": {"$regex": event_name, "$options": "i"}}}
                ],
                "as": "transaction_details"
            }
        },
        {"$unwind": "$transaction_details"},
        {
            "$group": {
                "_id": {
                    "year": {"$year": "$transaction_details.TransDate"},
                    "month": {"$month": "$transaction_details.TransDate"},
                    "day": {"$dayOfMonth": "$transaction_details.TransDate"}
                },
                "TicketsSold": {"$sum": 1}
            }
        },
        {"$sort": {"_id": 1}}
    ])

    ticket_sales_results = list(ticket_sales_results)
    ticket_sales_data = {
        'dates': [f"{result['_id']['year']}-{result['_id']['month']:02d}-{result['_id']['day']:02d}" for result in ticket_sales_results],
        'values': [result["TicketsSold"] for result in ticket_sales_results]
    }
    # Query revenue data for the selected event
    revenue_results = mongo.db.tickets.aggregate([
    {
        "$lookup": {
            "from": "events",
            "localField": "EventID",
            "foreignField": "id",
            "as": "event"
        }
    },
    {"$unwind": "$event"},
    {
        "$lookup": {
            "from": "ticketCategories",
            "let": {"category_id": {"$toObjectId": "$CatID"}},
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$eq": ["$_id", "$$category_id"]
                        }
                    }
                }
            ],
            "as": "category"
        }
    },
    {"$unwind": "$category"},
    {
        "$match": {
            "event.name": {"$regex": event_name, "$options": "i"}
        }
    },
    {
        "$group": {
            "_id": "$event.name",
            "TotalRevenue": {"$sum": "$category.CatPrice"}
        }
    }
    ])
    revenue_results = list(revenue_results)
    revenue_data = {
        'events': [result["_id"] for result in revenue_results],
        'values': [result["TotalRevenue"] for result in revenue_results]
    }

    return jsonify({'ticket_sales_data': ticket_sales_data, 'revenue_data': revenue_data})

@app.route('/profile/<string:user_id>', methods=['GET', 'POST'])
def profile(user_id):
    # Check if the user is logged in
    if 'user_id' not in session:
        return redirect(url_for('registersignup'))  # Redirect to login if not authenticated

    # Get the current user's ID from the session
    current_user_id = session['user_id']

    # If the logged-in user ID does not match the requested user ID, deny access
    if current_user_id != user_id:
        return "Access Denied", 403  # Return an error message or redirect to an error page

    # Fetch the user from MongoDB
    object_id = ObjectId(user_id)
    user = mongo.db.users.find_one({"_id": object_id})
    if user:
        # Convert ObjectId to string here
        user['_id'] = str(user['_id'])
    else:
        return "User not found", 404  # Return a 404 error if the user does not exist

    # Fetch the payment method for the user
    payment_method = mongo.db.paymentMethods.find_one({'user_id': user_id})
    if payment_method and 'ExpireDate' in payment_method:
            payment_method['ExpireDate'] = datetime.strptime(payment_method['ExpireDate'], '%Y-%m-%d')

    current_year = datetime.now().year

    # If no payment method exists, provide placeholders for template
    if payment_method is None:
        payment_method = {
            'CardHolderName': 'N/A',
            'CardNumber': '0000000000000000',
            'ExpireDate': None,  
            'BillAddr': 'No billing address available',
            'CVV': '000'  
        }

    # Render the profile page with user and payment method information
    return render_template('profile.html', user=user, paymentMethod=payment_method, current_year=current_year)

@app.route('/profile/<string:user_id>/update', methods=['POST'])
def update_profile(user_id):
    # Check if the user is logged in
    if 'user_id' not in session:
        return redirect(url_for('login'))  # Redirect to login if not authenticated

    # Get the current user's ID from the session
    current_user_id = session['user_id']

    # Ensure the logged-in user is the same as the user being updated
    if current_user_id != user_id:
        return "Access Denied", 403

    # Fetch the user from the MongoDB `users` collection
    object_id = ObjectId(user_id)
    user = mongo.db.users.find_one({"_id": object_id})
    if user:
        # Convert ObjectId to string here
        user['_id'] = str(user['_id'])
    if not user:
        return "User not found", 404

    # Get data from form submission
    new_name = request.form.get('name')
    new_email = request.form.get('email')
    new_phone = request.form.get('phone')
    new_password = request.form.get('password')

    # Prepare the updated fields
    updated_fields = {
        'Name': new_name,
        'Email': new_email,
        'Phone': new_phone
    }

    # If the user enters a new password, hash it and add to updated fields
    if new_password:
        hashed_password = generate_password_hash(new_password)
        updated_fields['Password'] = hashed_password

    # Update the user's information in MongoDB
    try:
        mongo.db.users.update_one(
            {'_id': object_id},
            {'$set': updated_fields}
        )
        flash('Profile updated successfully!', 'success')
    except Exception as e:
        flash(f'Error updating profile: {str(e)}', 'danger')

    return redirect(url_for('profile', user_id=user_id))

@app.route('/profile/<string:user_id>/deactivate', methods=['POST'])
def deactivate_account(user_id):
    # Check if the user is logged in
    if 'user_id' not in session:
        return redirect(url_for('login'))  # Redirect to login if not authenticated

    # Get the current user's ID from the session
    current_user_id = session['user_id']

    # Ensure the logged-in user is the same as the user being deactivated
    if current_user_id != user_id:
        return "Access Denied", 403

    try:
        # Delete the user document from the MongoDB users collection
        object_id = ObjectId(user_id)
        result = mongo.db.users.delete_one({'_id': object_id})

        if result.deleted_count > 0:
            flash('Account deactivated successfully!', 'success')
            session.pop('user_id', None)  # Remove user from session after deactivation
        else:
            flash('Error: Account not found or already deactivated.', 'danger')

    except Exception as e:
        flash(f'Error deactivating account: {str(e)}', 'danger')

    return redirect(url_for('login'))

@app.route('/update_payment/<string:user_id>', methods=['POST'])
def update_payment(user_id):
    # Fetch the payment method for the given user from MongoDB
    payment_method = mongo.db.paymentMethods.find_one({'user_id': user_id})

    if not payment_method:
        flash('Payment method not found!', 'danger')
        return redirect(url_for('profile', user_id=user_id))

    # Update the payment method fields from the form data
    updated_data = {
        'CardHolderName': request.form['cardHolderName'],
        'CardNumber': request.form['cardNumber'],
        'BillAddr': request.form['billingAddress'],
    }

    # Combine the month and year into an expiration date
    expire_month = request.form['expireDateMonth']
    expire_year = request.form['expireDateYear']
    expire_date_str = f"{expire_month}/01/{expire_year}"  # Assuming the first day of the month
    updated_data['expire_date'] = datetime.strptime(expire_date_str, '%m/%d/%Y')

    # Update CVV only if provided and not the placeholder
    if request.form['cvv'] and request.form['cvv'] != '***':
        updated_data['cvv'] = request.form['cvv']

    try:
        mongo.db.paymentMethods.update_one(
            {'user_id': user_id},
            {'$set': updated_data}
        )
        flash('Payment method updated successfully!', 'success')
    except Exception as e:
        flash(f'Error updating payment method: {str(e)}', 'danger')

    return redirect(url_for('profile', user_id=user_id))


@app.route('/add_payment/<string:user_id>', methods=['POST'])
def add_payment(user_id):
    # Fetch the user from the MongoDB `users` collection
    object_id = ObjectId(user_id)
    user = mongo.db.users.find_one({"_id": object_id})
    if user:
        # Convert ObjectId to string here
        user['_id'] = str(user['_id'])

    if not user:
        flash('User not found!', 'danger')
        return redirect(url_for('registersignup'))

    # Get the form data
    card_holder_name = request.form['cardHolderName']
    card_number = request.form['cardNumber']
    
    # Combine the month and year into an expiration date
    expire_month = request.form['expireDateMonth']
    expire_year = request.form['expireDateYear']
    expire_date_str = f"{expire_month}/01/{expire_year}"  # Assuming the first day of the month
    expire_date = datetime.strptime(expire_date_str, '%m/%d/%Y')
    
    billing_address = request.form['billingAddress']
    cvv = request.form['cvv']

    # Create the new payment method document
    new_payment_method = {
        'user_id': user_id,
        'CardHolderName': card_holder_name,
        'CardNumber': card_number,
        'ExpireDate': expire_date,
        'BillAddr': billing_address,
        'CVV': cvv,
        'CardType': "Visa"  # This can be determined dynamically if needed
    }

    try:
        # Insert the new payment method into the MongoDB collection
        mongo.db.paymentMethods.insert_one(new_payment_method)
        flash('Payment method added successfully!', 'success')
    except Exception as e:
        flash(f'Error adding payment method: {str(e)}', 'danger')

    return redirect(url_for('profile', user_id=user_id))

@app.route('/delete_payment/<string:user_id>', methods=['POST'])
def delete_payment(user_id):
    # Find the payment method for the given user in MongoDB
    payment_method = mongo.db.paymentMethods.find_one({'user_id': user_id})

    if payment_method:
        try:
            # Remove the payment method from the `paymentMethods` collection
            mongo.db.paymentMethods.delete_one({'user_id': user_id})

            # Set `card_id` to `None` for all transactions associated with this payment method
            mongo.db.transactions.update_many(
                {'card_id': payment_method.get('user_id')},
                {'$set': {'card_id': None}}
            )

            flash('Payment method deleted successfully!', 'success')
        except Exception as e:
            flash(f'Error deleting payment method: {str(e)}', 'danger')
    
    else:
        flash('Payment method not found!', 'danger')

    return redirect(url_for('profile', user_id=user_id))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)