from flask import Flask, render_template, request, session, url_for, redirect, jsonify, make_response, flash
from config import Config
from sqlalchemy import text, extract,and_, func
from sqlalchemy.orm import joinedload
from sqlalchemy.sql import exists
from api.ticketmaster import fetch_and_store_events
import logging, traceback
from datetime import datetime
import calendar
import bcrypt
from auth import hash_password, verify_password, RegistrationForm, LoginForm
import calendar
from models import db, Users, PaymentMethod, Location, Event, Ticket, TicketCategory, Transactions, Image, Queue
from werkzeug.security import generate_password_hash 

app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = Config.APP_SECRET_KEY
app.config.from_object(Config)
db.init_app(app)

API_KEY = Config.TICKETMASTER_API_KEY

# Check connection and create tables if not already created
with app.app_context():
    events_to_fetch = 20
    try:
        # Test the connection to the database
        with db.engine.connect() as connection:
            result = connection.execute(text("SELECT 1")).scalar()
            print(f"Connection to database successful! Result: {result}")
        
        # Check if tables exist
        inspector = db.inspect(db.engine)
        tables = inspector.get_table_names()

        if not tables:
            db.create_all()
            logging.info("Tables created successfully!")

        current_event_count = db.session.query(Event).count()
        print(f"Currently {current_event_count} events in the database.")

        if current_event_count < events_to_fetch:
            print(f"Less than {events_to_fetch} events found in the database, fetching more events...")
            fetch_and_store_events(API_KEY, events_to_fetch - current_event_count)
        else:
            print("Sufficient events are already stored in the database, no action needed.")
    except Exception as e:
        logging.error(f"Error during database setup or event fetching: {e}\n{traceback.format_exc()}")

# Routes
@app.route('/')
def home():
    preferred_width = 1920

    # Query to fetch events with the most transactions
    hot_events_info = db.session.query(
        Event.EventID,
        Event.EventName,
        func.count(Transactions.TranscID).label('transaction_count')
    ).join(Event.ticketCategory) \
      .join(TicketCategory.ticket) \
      .join(Ticket.transaction) \
      .group_by(Event.EventID) \
      .order_by(func.count(Transactions.TranscID).desc()) \
      .limit(6) \
      .all()

      # Check if enough events were fetched
    if len(hot_events_info) < 6:
        # Fetch more events to make the total count 6
        additional_events_needed = 6 - len(hot_events_info)
        more_events = Event.query \
            .filter(Event.EventID.notin_([event.EventID for event in hot_events_info])) \
            .limit(additional_events_needed) \
            .all()
        hot_events_info.extend([(event.EventID, event.EventName, 0) for event in more_events])

    # Prepare data to render
    hot_events = []
    for event_info in hot_events_info:
        event_id, event_name, _ = event_info
        event = Event.query.options(joinedload(Event.image)).filter_by(EventID=event_id).first()
        if event.image:
            event.preferred_image = min(event.image, key=lambda img: abs(img.Width - preferred_width))
            image_url = event.preferred_image.URL
        else:
            image_url = url_for('static', filename='images/default.jpg')

        hot_events.append({
            'EventID': event_id,
            'EventName': event_name,
            'ImageURL': image_url
        })

    # Fetch the most popular venues based on the number of associated events
    top_venues_query = db.session.query(
        Location.LocationID,
        Location.VenueName,
        db.func.count(Event.EventID).label('event_count'),
    ).join(Location.event) \
     .outerjoin(Location.image) \
     .group_by(Location.LocationID) \
     .order_by(db.func.count(Event.EventID).desc()) \
     .limit(6)

    top_venues = top_venues_query.all()

    # Ensure there are 6 venues
    if len(top_venues) < 6:
        additional_venues_needed = 6 - len(top_venues)
        additional_venues = Location.query \
            .filter(Location.LocationID.notin_([venue.LocationID for venue in top_venues])) \
            .limit(additional_venues_needed) \
            .all()
        top_venues.extend([(venue.LocationID, venue.VenueName, 0, None) for venue in additional_venues])

    # Format the fetched venues
    formatted_venues = []
    for venue in top_venues:
        location_id, venue_name, event_count = venue
        location = Location.query.options(joinedload(Location.image)).filter_by(LocationID=location_id).first()
        if location.image:
            # Choosing the best image based on width preference
            preferred_image = min(location.image, key=lambda img: abs(img.Width - preferred_width))
            image_url = preferred_image.URL
        else:
            image_url = url_for('static', filename='images/default.jpg')
        
        formatted_venues.append({
            'LocationID': location_id,
            'VenueName': venue_name,
            'ImageURL': image_url
        })

    return render_template('landing.html', hot_events=hot_events, venues=formatted_venues)

@app.route('/event')
def event():
    search_query = request.args.get('search', '').lower() 
    search_month = request.args.get('search_month')  
    page = request.args.get('page', 1, type=int)  
    events_per_page = 9  
    events_by_month_year = {}
    preferred_width = 1920
    search_date_parsed = None

    if search_month:
        try:
            search_date_parsed = datetime.strptime(search_month, '%Y-%m')
        except ValueError:
            search_date_parsed = None  

    base_query = Event.query.options(joinedload(Event.image)).order_by(Event.EventDate)

    if search_query and search_date_parsed:
        base_query = base_query.filter(
            and_(
                Event.EventName.ilike(f'%{search_query}%'),
                Event.EventDate.between(search_date_parsed.replace(day=1),
                                        search_date_parsed.replace(day=calendar.monthrange(search_date_parsed.year, search_date_parsed.month)[1]))
            )
        )
    elif search_query:
        base_query = base_query.filter(Event.EventName.ilike(f'%{search_query}%'))
    elif search_date_parsed:
        base_query = base_query.filter(
            Event.EventDate.between(search_date_parsed.replace(day=1),
                                    search_date_parsed.replace(day=calendar.monthrange(search_date_parsed.year, search_date_parsed.month)[1]))
        )

    # Pagination
    paginated_events = base_query.paginate(page=page, per_page=events_per_page)

    for event in paginated_events.items:
        if event.image:
            event.preferred_image = min(event.image, key=lambda img: abs(img.Width - preferred_width))
        else:
            event.preferred_image = None

        # Group events by month and year
        month_year = f"{calendar.month_name[event.EventDate.month]} {event.EventDate.year}"
        if month_year not in events_by_month_year:
            events_by_month_year[month_year] = []
        events_by_month_year[month_year].append(event)

    sorted_month_years = sorted(
        events_by_month_year.keys(),
        key=lambda x: (int(x.split(' ')[1]), -1 if int(x.split(' ')[1]) == datetime.now().year else 0)
    )

    return render_template(
        'event.html',
        events_by_month_year={key: events_by_month_year[key] for key in sorted_month_years},
        search_query=search_query,
        search_month=search_month, 
        current_page=page,
        total_pages=paginated_events.pages
    )

@app.route('/venue')
def venue():
    preferred_width = 1920
    page = request.args.get('page', 1, type=int)
    per_page = 12

    search_query = request.args.get('search', '')

    offset = (page - 1) * per_page

    sql_query = """
        SELECT "Location"."LocationID", "Location"."VenueName", "Location"."Address", "Location"."Country", "State", "PostalCode", 
        "Image"."URL", "Image"."Width" 
        FROM "Location" 
        LEFT JOIN "Image" ON "Location"."LocationID" = "Image"."LocationID"
        WHERE (:search_query = '' OR "Location"."VenueName" ILIKE :search_query)
        LIMIT :per_page OFFSET :offset
    """

    locations = db.session.execute(
        text(sql_query),
        {
            'search_query': f"%{search_query}%" if search_query else '',
            'per_page': per_page,
            'offset': offset
        }
    ).fetchall()

    venues_with_images = []

    for location in locations:
        venue = {
            'LocationID': location.LocationID,
            'VenueName': location.VenueName,  # Ensure the VenueName is passed
            'Address': location.Address,
            'Country': location.Country,
            'State': location.State,
            'PostalCode': location.PostalCode,
            'ImageURL': location.URL if location.URL else 'static/images/venue1.jpg'
        }
        venues_with_images.append(venue)

    count_query = """
        SELECT COUNT(*) 
        FROM "Location" 
        WHERE (:search_query = '' OR "Location"."VenueName" ILIKE :search_query)
    """
    total_venues = db.session.execute(
        text(count_query),
        {'search_query': f"%{search_query}%" if search_query else ''}
    ).scalar()

    total_pages = (total_venues + per_page - 1) // per_page
    pagination = {
        'page': page,
        'total_pages': total_pages,
        'has_next': page < total_pages,
        'has_prev': page > 1,
        'next_num': page + 1 if page < total_pages else None,
        'prev_num': page - 1 if page > 1 else None,
    }

    return render_template('venue.html', venues=venues_with_images, pagination=pagination, search_query=search_query)

@app.route('/venueinfo/<LocationID>')
def venueinfo(LocationID):
    venue = Location.query.filter_by(LocationID=LocationID).first()
    if not venue:
        return "Venue not found", 404

    image_url = venue.image[0].URL if venue.image else 'static/images/venue1.jpg'  # Use a default image if none is found

    return render_template('venueinfo.html', venue=venue, image_url=image_url)

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
        existing_user = Users.query.filter_by(Email=email).first()
        if existing_user:
            error_message = 'Email already registered.'
            return render_template('registersignup.html', registration_form=registration_form, login_form=login_form, error_message=error_message)

        hashed_password = bcrypt.hashpw(registration_form.password.data.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        new_user = Users(
            Name=registration_form.name.data,
            Email=email,
            Password=hashed_password,
            Phone=registration_form.phone.data
        )
        
        db.session.add(new_user)
        db.session.commit()

        error_message = 'You have successfully registered!'
        return render_template('landing.html', error_message=error_message)

    else:
        logging.error("Form Errors:", registration_form.errors)
        error_message = ""  # Initialize error_message to avoid undefined error
        for field, errors in registration_form.errors.items():
            for error in errors:
                error_message += f"Error in the {getattr(registration_form, field).label.text} field - {error} "

    return render_template('registersignup.html', registration_form=registration_form, login_form=login_form, error_message=error_message)

@app.route('/login', methods=['POST'])
def login():
    login_form = LoginForm(request.form)
    registration_form = RegistrationForm()

    email = login_form.email.data
    password = login_form.password.data
    
    if login_form.validate_on_submit():
        user = Users.query.filter_by(Email=email).first()

        if user and bcrypt.checkpw(login_form.password.data.encode('utf-8'), user.Password.encode('utf-8')):
            session['user_id'] = user.UserID
            error_message = 'Login successful!'
            return render_template('landing.html', error_message=error_message)
        else:
            error_message = 'Invalid email or password'
            return render_template('registersignup.html', login_form=login_form, registration_form=registration_form, error_message=error_message)
    else:
        logging.error("Form Errors:", login_form.errors) 
        for fieldName, errorMessages in login_form.errors.items():
            for err in errorMessages:
                error_message += f'{fieldName}: {err} '

    return render_template('registersignup.html', login_form=login_form, registration_form=registration_form, error_message=error_message) 

@app.route('/logout')
def logout():
    session.pop('user_id', None)  # Remove user_id from session
    error_message = 'You have been logged out.'
    return render_template('landing.html', error_message=error_message)

@app.route('/myticket')
def myticket():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('registersignup'))

    # Load transactions and related tickets, ticket categories, and events
    transactions = Transactions.query.options(
        joinedload(Transactions.ticket)
        .joinedload(Ticket.ticketCategory)
        .joinedload(TicketCategory.event)
    ).filter(Transactions.UserID == user_id).all()

    # Prepare data for the template
    ticket_details = []
    for transaction in transactions:
        for ticket in transaction.ticket:
            event = ticket.ticketCategory.event if ticket.ticketCategory else None
            if event:
                ticket_info = {
                    'TranscID': transaction.TranscID,
                    'TransDate': transaction.TransDate.strftime('%d-%m-%Y %I:%M%p'),
                    'EventName': event.EventName,
                    'SeatNo': ticket.SeatNo,
                    'Status': 'upcoming' if event.EventDate > datetime.utcnow() else 'finished'
                }
                ticket_details.append(ticket_info)

    return render_template('myticket.html', ticket_details=ticket_details)

@app.route('/ticket/<event_id>')
def ticket(event_id):
    error_message = None
    preferred_width = 1920

    # Get user info from session
    user_id = session.get('user_id')
    user = Users.query.get(user_id)
    event = Event.query.options(joinedload(Event.image), joinedload(Event.ticketCategory)).filter_by(EventID=event_id).first()

    if not event:
        return redirect(url_for('landing'))

    # Determine ticket availability
    tickets_available = any(
        category.SeatsAvailable > Ticket.query.filter_by(CatID=category.CatID).count()
        for category in event.ticketCategory
    )

    # Choose preferred image based on width
    if event.image:
        event.preferred_image = min(event.image, key=lambda img: abs(img.Width - preferred_width))
    else:
        image_url = url_for('static', filename='images/default.jpg')

    # Prepare event and user information for the template
    event_image = {
        'ImageURL': event.preferred_image.URL if event.preferred_image else url_for('static', filename='images/default.jpg')
    }
    return render_template('ticket.html', 
                           event=event,
                           calendar=calendar,
                           event_image=event_image,
                           user=user,
                           tickets_available=tickets_available,
                           payment_method=PaymentMethod.query.filter_by(UserID=user_id).first())


@app.route('/ticket_purchase/<event_id>', methods=['POST'])
def ticket_purchase(event_id):

    # Check if the user is logged in
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('registersignup')) 
    
    event = Event.query.filter_by(EventID=event_id).first()
    if not event:
        return redirect(url_for('landing'))
    
    ticket_categories = event.ticketCategory
    if not ticket_categories:
        return redirect(url_for('event', event_id=event_id))
    
    try:
        category_id = request.form.get('category')
        quantity = int(request.form.get('quantity'))
        ticket_category = TicketCategory.query.get(category_id)

        if not ticket_category:
            # flash('Ticket category not found.', 'error')
            return redirect(url_for('event', event_id=event_id))        
        # elif ticket_category.SeatsAvailable < quantity:
        #     flash('Not enough tickets available', 'error')

        cardholder_name = request.form.get('cardholder-name')
        card_number = request.form.get('card-number')
        cvv = request.form.get('cvv')
        expiry_month = request.form.get('expiry-month')
        expiry_year = request.form.get('expiry-year')
        billing_address = request.form.get('billing-address')

        payment_method = PaymentMethod.query.filter_by(UserID=user_id).first()
        if payment_method:
            # Update existing payment method
            payment_method.CVV = cvv
            payment_method.ExpireDate = datetime(int(expiry_year), int(expiry_month), 1)
            payment_method.BillAddr = billing_address
            payment_method.CardHolderName = cardholder_name
        else:
            # Create new payment method
            payment_method = PaymentMethod(
                UserID=user_id,
                CardNumber=card_number,
                CVV=cvv,
                CardType='Unknown',
                ExpireDate=datetime(int(expiry_year), int(expiry_month), 1),
                BillAddr=billing_address,
                CardHolderName=cardholder_name
            )
            db.session.add(payment_method)

        db.session.commit()

        # Create transaction
        total_price = ticket_category.CatPrice * quantity
        transaction = Transactions(
            TranAmount=total_price,
            TranStatus='Completed',
            UserID=user_id,
            CardID=payment_method.CardID
        )
        db.session.add(transaction)
        db.session.flush()

    # Create tickets
        tickets = []
        highest_seat = db.session.query(db.func.max(Ticket.SeatNo)).filter_by(CatID=category_id).scalar()
        highest_seat = highest_seat or 0
        if highest_seat >= ticket_category.SeatsAvailable:
            # flash('Not enough tickets available', 'error')
            return redirect(url_for('event', event_id=event_id))
        
        start_seat_number = highest_seat + 1
        for i in range(quantity):
            seat_number = start_seat_number + i
            ticket = Ticket(
                CatID=category_id,
                EventID=event_id,
                SeatNo=seat_number,
                Status='Issued',
                TranscID=transaction.TranscID  # Use the TranscID from the transaction
            )
            tickets.append(ticket)
            ticket_category.SeatsAvailable -= 1
        
        db.session.add_all(tickets)
        db.session.commit()

        # flash('Purchase successful!', 'success')
        return redirect(url_for('myticket'))

    except Exception as e:
            db.session.rollback()
            logging.error(f"Error during ticket purchase: {e}\n{traceback.format_exc()}")
            # flash('An error occurred during the purchase. Please try again.', 'error')
            return redirect(url_for('event', event_id=event_id))

@app.route('/queue/<event_id>')
def queue(event_id):
    # Check if the user is logged in
    user_id = session.get('user_id')
    error_message = None
    preferred_width = 1920

    if not user_id:
        return redirect(url_for('registersignup'))
    
    else:
        event = Event.query.options(joinedload(Event.image), joinedload(Event.ticketCategory)).filter_by(EventID=event_id).first()
        # Choose preferred image based on width
        if event.image:
            event.preferred_image = min(event.image, key=lambda img: abs(img.Width - preferred_width))
        else:
            image_url = url_for('static', filename='images/default.jpg')

        # Prepare event and user information for the template
        event_image = {
            'ImageURL': event.preferred_image.URL if event.preferred_image else url_for('static', filename='images/default.jpg')
        }

        return render_template('enterqueue.html', event_image=event_image, event=event )

@app.route('/joinqueue/<event_id>', methods=['POST'])
def joinqueue(event_id):
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('registersignup'))
    user = Users.query.get(user_id)
    if not user:
        error_message = 'Please log in first'
        return render_template('registersignup.html', error_message=error_message, registration_form=RegistrationForm(), login_form=LoginForm())
    
    error_message = None
    preferred_width = 1920
    event = Event.query.options(joinedload(Event.image), joinedload(Event.ticketCategory)).filter_by(EventID=event_id).first()
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('landing'))

    existing_queue = Queue.query.filter_by(UserID=user_id, EventID=event_id).first()
    if existing_queue:
        flash('You are already in the queue for this event.', 'info')
        return redirect(url_for('event', event_id=event_id))
    if event.image:
        event.preferred_image = min(event.image, key=lambda img: abs(img.Width - preferred_width))
    else:
        image_url = url_for('static', filename='images/default.jpg')
    
    event_image = {
        'ImageURL': event.preferred_image.URL if event.preferred_image else url_for('static', filename='images/default.jpg')
    }

    try:
        new_queue = Queue(
            UserID=user_id,
            EventID=event_id,
        )
        db.session.add(new_queue)
        db.session.commit()

        queueNo = new_queue.QueueID

        data = {
            'UserID': user_id,
            'EventID': event_id,
            'QNo': queueNo
        }

    except Exception as e:
        db.session.rollback()
        flash('Failed to join the queue. Please try again.', 'error')
        logging.error(f"Error during queue addition: {e}")
    
    return render_template('queue.html', data=data, event_image=event_image, event=event)


@app.route('/joinqueue/<event_id>/inqueue/<queue_id>')
def inqueue(event_id, queue_id):
    user_id = session.get('user_id')

    event = Event.query.options(joinedload(Event.image), joinedload(Event.ticketCategory)).filter_by(EventID=event_id).first()
    if not event:
        return redirect(url_for('landing'))

    # Preferred image selection
    preferred_width = 1920
    if event.image:
        event.preferred_image = min(event.image, key=lambda img: abs(img.Width - preferred_width))
    else:
        image_url = url_for('static', filename='images/default.jpg')

    event_image = {
        'ImageURL': event.preferred_image.URL if event.preferred_image else url_for('static', filename='images/default.jpg')
    }

    # Retrieve top user in queue
    topQueue = db.session.query(Queue).filter(Queue.EventID == event_id).first()

    if topQueue:
        topUser = topQueue.UserID

        # Check if the logged-in user is at the top of the queue
        if topUser == user_id:
            user = Users.query.get(user_id)

            # Determine ticket availability
            tickets_available = any(
                category.SeatsAvailable > Ticket.query.filter_by(CatID=category.CatID).count()
                for category in event.ticketCategory
            )

            # Optionally remove the user from the queue after they receive tickets or proceed
            db.session.delete(topQueue)  # Only delete if the process completes
            db.session.commit()
        
            return render_template('ticket.html', 
                                event=event,
                                calendar=calendar,
                                event_image=event_image,
                                user=user,
                                tickets_available=tickets_available,
                                payment_method=PaymentMethod.query.filter_by(UserID=user_id).first())

        else:
            # The current user is not the top user, just re-render queue page
            data = {
                'UserID': user_id,
                'EventID': event_id,
                'QNo': queue_id
            }
            return render_template('queue.html', data=data, event_image=event_image, event=event)

    else:
        return redirect(url_for('queue', event_id=event_id))


@app.context_processor
def inject_user():
    user_id = session.get('user_id')
    user = None
    if user_id:
        user = Users.query.get(user_id)
    return dict(user=user)

@app.route('/aboutus', methods=['GET', 'POST'])
def aboutus():
    # Query for most popular event
    most_popular_event = db.session.execute(text("""
        SELECT e."EventName", COUNT(t."TicketID") AS "TicketsSold"
        FROM "Ticket" t
        JOIN "Event" e ON t."EventID" = e."EventID"
        GROUP BY e."EventName"
        ORDER BY "TicketsSold" DESC
        LIMIT 1;
    """)).fetchone()

    # Query for total tickets sold
    total_tickets_sold = db.session.execute(text("""
        SELECT COUNT("TicketID") AS "TotalTicketsSold" 
        FROM "Ticket";
    """)).scalar()

    # Query for total unique events and locations
    total_events_locations = db.session.execute(text("""
        SELECT COUNT(DISTINCT "EventID") AS "TotalEvents", 
               COUNT(DISTINCT "LocationID") AS "TotalLocations"
        FROM "Event";
    """)).fetchone()

    # Query for ticket sales data (Line Chart)
    ticket_sales_results = db.session.execute(text("""
        SELECT e."EventDate", COUNT(t."TicketID") AS "TicketsSold"
        FROM "Ticket" t
        JOIN "Event" e ON t."EventID" = e."EventID"
        GROUP BY e."EventDate"
        ORDER BY e."EventDate";
    """)).fetchall()

    ticket_sales_dates = [result[0].strftime('%Y-%m-%d') for result in ticket_sales_results]
    ticket_sales_data = [result[1] for result in ticket_sales_results]

    # Query for revenue data (Bar Chart)
    revenue_results = db.session.execute(text("""
        SELECT e."EventName", SUM(tc."CatPrice") AS "TotalRevenue"
        FROM "Ticket" t
        JOIN "TicketCategory" tc ON t."CatID" = tc."CatID"
        JOIN "Event" e ON t."EventID" = e."EventID"
        GROUP BY e."EventName";
    """)).fetchall()

    revenue_event_names = [result[0] for result in revenue_results]
    revenue_data = [result[1] for result in revenue_results]

    # Query for ticket categories (Pie Chart)
    category_results = db.session.execute(text("""
        SELECT tc."CatName", COUNT(t."TicketID") AS "TotalTickets"
        FROM "Ticket" t
        JOIN "TicketCategory" tc ON t."CatID" = tc."CatID"
        GROUP BY tc."CatName";
    """)).fetchall()

    category_names = [result[0] for result in category_results]
    category_data = [result[1] for result in category_results]

    # Query for events by location (Doughnut Chart)
    location_results = db.session.execute(text("""
        SELECT l."VenueName", COUNT(e."EventID") AS "EventsCount"
        FROM "Event" e
        JOIN "Location" l ON e."LocationID" = l."LocationID"
        GROUP BY l."VenueName";
    """)).fetchall()

    location_names = [result[0] for result in location_results]
    location_data = [result[1] for result in location_results]

    # Query for all event names (for the search dropdown)
    event_list = db.session.execute(text("""
        SELECT DISTINCT e."EventName" FROM "Event" e;
    """)).fetchall()

    # Query for all categories (for the filter dropdown)
    category_list = db.session.execute(text("""
        SELECT DISTINCT tc."CatName" FROM "TicketCategory" tc;
    """)).fetchall()

    # Query for ticket sales data for the default event (Phoenix Suns vs. Miami Heat)
    default_event = 'Phoenix Suns vs. Portland Trail Blazers'
    ticket_sales_results_default = db.session.execute(text("""
        SELECT e."EventDate", COUNT(t."TicketID") AS "TicketsSold"
        FROM "Ticket" t
        JOIN "Event" e ON t."EventID" = e."EventID"
        WHERE LOWER(e."EventName") LIKE :event_name
        GROUP BY e."EventDate"
        ORDER BY e."EventDate";
    """), {'event_name': f"%{default_event.lower()}%"}).fetchall()

    ticket_sales_dates_default = [result[0].strftime('%Y-%m-%d') for result in ticket_sales_results_default]
    ticket_sales_data_default = [result[1] for result in ticket_sales_results_default]

    # Query revenue data for the default event
    revenue_results_default = db.session.execute(text("""
        SELECT e."EventName", SUM(tc."CatPrice") AS "TotalRevenue"
        FROM "Ticket" t
        JOIN "TicketCategory" tc ON t."CatID" = tc."CatID"
        JOIN "Event" e ON t."EventID" = e."EventID"
        WHERE LOWER(e."EventName") LIKE :event_name
        GROUP BY e."EventName";
    """), {'event_name': f"%{default_event.lower()}%"}).fetchall()

    revenue_event_names_default = [result[0] for result in revenue_results_default]
    revenue_data_default = [result[1] for result in revenue_results_default]

    # Query to get average daily sales per hour
    purchase_times_results = db.session.execute(text("""
        WITH HourlySales AS (
            SELECT
                DATE(t."TransDate") AS sale_date,
                EXTRACT(HOUR FROM t."TransDate") AS sale_hour,
                COUNT(*) AS num_sales
            FROM "Transactions" t
            GROUP BY sale_date, sale_hour
        )
        SELECT sale_hour, AVG(num_sales) AS avg_sales
        FROM HourlySales
        GROUP BY sale_hour
        ORDER BY sale_hour;
    """)).fetchall()

    purchase_hours = [f"{int(result[0]):02}:00" for result in purchase_times_results]
    average_sales = [float(result[1]) for result in purchase_times_results]

    # Query for revenue per event type
    revenue_per_event_type_results = db.session.execute(text("""
        SELECT e."EventType", SUM(tc."CatPrice") AS "TotalRevenue"
        FROM "Ticket" t
        JOIN "TicketCategory" tc ON t."CatID" = tc."CatID"
        JOIN "Event" e ON t."EventID" = e."EventID"
        GROUP BY e."EventType"
    """)).fetchall()

    event_types = [result[0] for result in revenue_per_event_type_results]
    revenues = [float(result[1]) for result in revenue_per_event_type_results]
    
    # Debugging print statements
    print("Ticket Sales Dates for Default Event:", ticket_sales_dates_default)
    print("Ticket Sales Data for Default Event:", ticket_sales_data_default)
    print("Revenue Event Names for Default Event:", revenue_event_names_default)
    print("Revenue Data for Default Event:", revenue_data_default)

    return render_template('aboutus.html', 
                        event_types=event_types,
                        revenues=revenues,
                        purchase_hours=purchase_hours,
                        average_sales=average_sales,
                        most_popular_event=most_popular_event, 
                        total_tickets_sold=total_tickets_sold,
                        total_events_locations=total_events_locations,
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

    # Query ticket sales for the selected event
    ticket_sales_results = db.session.execute(text("""
        SELECT e."EventDate", COUNT(t."TicketID") AS "TicketsSold"
        FROM "Ticket" t
        JOIN "Event" e ON t."EventID" = e."EventID"
        WHERE LOWER(e."EventName") LIKE :event_name
        GROUP BY e."EventDate"
        ORDER BY e."EventDate";
    """), {'event_name': f"%{event_name}%"}).fetchall()

    ticket_sales_data = {
        'dates': [result[0].strftime('%Y-%m-%d') for result in ticket_sales_results],
        'values': [float(result[1]) for result in ticket_sales_results]
    }

    # Query revenue data for the selected event
    revenue_results = db.session.execute(text("""
        SELECT e."EventName", SUM(tc."CatPrice") AS "TotalRevenue"
        FROM "Ticket" t
        JOIN "TicketCategory" tc ON t."CatID" = tc."CatID"
        JOIN "Event" e ON t."EventID" = e."EventID"
        WHERE LOWER(e."EventName") LIKE :event_name
        GROUP BY e."EventName";
    """), {'event_name': f"%{event_name}%"}).fetchall()

    revenue_data = {
        'events': [result[0] for result in revenue_results],
        'values': [float(result[1]) for result in revenue_results]
    }

    return jsonify({'ticket_sales_data': ticket_sales_data, 'revenue_data': revenue_data})


@app.route('/profile/<int:user_id>', methods=['GET', 'POST'])
def profile(user_id):
    user = Users.query.get_or_404(user_id)
    payment_method = PaymentMethod.query.filter_by(UserID=user_id).first()

    # If no payment method exists, provide placeholders for template
    if payment_method is None:
        payment_method = {
            'CardHolderName': 'N/A',
            'CardNumber': '0000000000000000',
            'ExpireDate': None,  
            'BillAddr': 'No billing address available',
            'CVV': '000'  
        }

    return render_template('profile.html', user=user, paymentMethod=payment_method)




# Route for updating the profile (separate POST request)
@app.route('/profile/<int:user_id>/update', methods=['POST'])
def update_profile(user_id):
    user = Users.query.get_or_404(user_id)

    # Get data from form submission
    new_name = request.form.get('name')
    new_email = request.form.get('email')
    new_phone = request.form.get('phone')
    new_password = request.form.get('password')

    # Update user's information
    try:
        user.Name = new_name
        user.Email = new_email
        user.Phone = new_phone

        # If the user enters a new password, hash it and update it
        if new_password:
            hashed_password = generate_password_hash(new_password)
            user.Password = hashed_password

        db.session.commit()
        flash('Profile updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating profile: {str(e)}', 'danger')

    return redirect(url_for('profile', user_id=user_id))

@app.route('/profile/<int:user_id>/deactivate', methods=['POST'])
def deactivate_account(user_id):
    user = Users.query.get_or_404(user_id)

    try:
        # Delete the user from the database
        db.session.delete(user)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Error deactivating account: {str(e)}', 'danger')

    return redirect(url_for('home'))
@app.route('/update_payment/<int:user_id>', methods=['POST'])
def update_payment(user_id):
    payment_method = PaymentMethod.query.filter_by(UserID=user_id).first()
    
    payment_method.CardHolderName = request.form['cardHolderName']
    payment_method.CardNumber = request.form['cardNumber']
    payment_method.ExpireDate = datetime.strptime(request.form['expireDate'], '%m/%Y')
    payment_method.BillAddr = request.form['billingAddress']

    if request.form['cvv'] and request.form['cvv'] != '***':
        payment_method.CVV = request.form['cvv']

    db.session.commit()
    flash('Payment method updated successfully!', 'success')

    return redirect(url_for('profile', user_id=user_id))

@app.route('/add_payment/<int:user_id>', methods=['POST'])
def add_payment(user_id):
    user = Users.query.get_or_404(user_id)

    card_holder_name = request.form['cardHolderName']
    card_number = request.form['cardNumber']
    expire_date = datetime.strptime(request.form['expireDate'], '%m/%Y')
    billing_address = request.form['billingAddress']
    cvv = request.form['cvv']

    new_payment_method = PaymentMethod(
        UserID=user.UserID,
        CardHolderName=card_holder_name,
        CardNumber=card_number,
        ExpireDate=expire_date,
        BillAddr=billing_address,
        CVV=cvv,
        CardType="Visa"  # Can be handled dynamically
    )

    db.session.add(new_payment_method)
    db.session.commit()

    flash('Payment method added successfully!', 'success')
    return redirect(url_for('profile', user_id=user_id))


if __name__ == "__main__":
    app.run(debug=True)
