import requests
from datetime import datetime, timezone
from models import db, Event, Image, Location, TicketCategory
import logging, traceback
from config import Config
from sqlalchemy import func
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

API_KEY = Config.TICKETMASTER_API_KEY

def fetch_and_store_events(api_key, total_events):
    base_url = 'https://app.ticketmaster.com/discovery/v2/events.json'
    event_type_counts = {'Music': 0, 'Sports': 0, 'Arts': 0, 'Theater': 0, 'Family': 0}
    event_type_limits = {
        'Music': total_events * 0.50,
        'Sports': total_events * 0.16,
        'Arts': total_events * 0.16,
        'Theater': total_events * 0.16,
        'Family': total_events * 0.16
    }
    events_fetched = 0
    page_number = 0
    seen_event_ids = set()
    seen_event_names = set()

    existing_event_names = {event.EventName for event in db.session.query(Event.EventName).all()}
    seen_event_names.update(existing_event_names)

    while events_fetched < total_events:
        params = {
            'apikey': api_key,
            'size': 50,
            'page': page_number
        }
        try:
            response = requests.get(base_url, params=params)
            response.raise_for_status()  # Will raise an exception for HTTP errors
            data = response.json()

            embedded = data.get('_embedded')
            if embedded and 'events' in embedded:
                for event in embedded['events']:
                    event_id = event['id']
                    event_name = event['name']
                    if event_id not in seen_event_ids and event_name not in seen_event_names:
                        event_type = event['classifications'][0]['segment']['name'] if 'classifications' in event and event['classifications'] else 'Undefined'
                        if event_type in event_type_counts and event_type_counts[event_type] < event_type_limits[event_type]:
                            store_event(event)
                            event_type_counts[event_type] += 1
                            events_fetched += 1
                            seen_event_ids.add(event_id)
                            seen_event_names.add(event_name)
                            logging.info(f"{events_fetched} events fetched: {event_type_counts}")
                    if events_fetched >= total_events:
                        break
            else:
                logging.warning(f"No events found in response on page {page_number}.")
        except requests.exceptions.HTTPError as e:
            logging.error(f"HTTP error occurred: {e.response.status_code} - {e.response.reason} for URL {e.response.url}")
            break  # Stop fetching more pages on client error like 400
        except requests.exceptions.RequestException as e:
            logging.error(f"Request failed: {e}")
        except Exception as e:
            logging.error(f"Unexpected error: {e}")

        if events_fetched+1 >= total_events:
            break
        page_number += 1

    logging.info("Finished fetching events.")

def parse_datetime(date_str):
    """ A replacement for datetime.fromisoformat for Python 3.7 """
    try:
        return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.strptime(date_str[:-6], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)

def store_event(event_data):  
    try:  
        # event_type = event_data['classifications'][0]['segment']['name'] if 'classifications' in event_data and event_data['classifications'] else 'Undefined'
        # event_date_str = event_data['dates']['start']['dateTime']
        # if event_date_str.endswith('Z'):  # Checks if the string ends with 'Z'
        #     event_date_str = event_date_str[:-1] + '+00:00'  # Replace 'Z' with '+00:00' which is the offset notation for UTC
        event_date = parse_datetime(event_data['dates']['start']['dateTime'])
                
        event = Event(
            EventID=event_data['id'],
            EventName=event_data['name'],
            EEventDate=event_date,
            EventType=event_type,
            LocationID=event_data['_embedded']['venues'][0]['id'],
        )
        locationID = Location.query.filter_by(LocationID=event.LocationID).first()
        if not locationID:
            fetch_venue_by_id(API_KEY, event.LocationID)
        db.session.add(event)
        db.session.flush()  # Flush to get the event ID for image relationship
        logging.info(f"Event name: {event.EventName}, type: {event.EventType}, start date: {event.EventDate}, venue name: {event.LocationID}, id: {event.EventID} stored successfully!")
        
        min_price = event_data['priceRanges'][0]['min']
        max_price = event_data['priceRanges'][0]['max']

        store_ticket_category(min_price, max_price, event.EventID)
        if 'images' in event_data:
            store_image(event_data['images'], None, event.EventID)

    except KeyError as e:
        logging.error(f"Missing expected key in event data: {e}")
    except Exception as e:
        logging.error(f"An error occurred while storing event: {e}")
        db.session.rollback()

def fetch_venue_by_id(api_key, venue_id):
    base_url = "https://app.ticketmaster.com/discovery/v2/venues"
    url = f"{base_url}/{venue_id}.json?apikey={api_key}"
    
    try:
        # Make the API request
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for HTTP errors
        
        # Parse the JSON response
        location_data = response.json()
        store_location(location_data)
        
    except requests.RequestException as e:
        logging.error(f"API request failed: {e}\n{traceback.format_exc()}")
    except Exception as e:
        logging.error(f"Error processing venue data: {e}\n{traceback.format_exc()}")

def store_location(location_data):
    try:
        new_location = Location(
            LocationID=location_data.get('id'),
            VenueName=location_data.get('name'),
            Address=location_data.get('address', {}).get('line1', 'No Address Provided'),
            Country=location_data.get('country', {}).get('name', 'No Country Provided'),
            State=location_data.get('state', {}).get('name', 'No State Provided'),
            PostalCode=location_data.get('postalCode', 'No Postal Code Provided'),
            # Capacity = None
        )
        db.session.add(new_location)
        db.session.commit()
        logging.info(f"LocationID: {new_location.LocationID}, VenueName: {new_location.VenueName}")
        if 'images' in location_data:
            store_image(location_data['images'], new_location.LocationID, None)
    except Exception as e:
        db.session.rollback()
        logging.error(f"Failed to store location. Error: {e}\n{traceback.format_exc()}")
   

def store_ticket_category(min_price, max_price, event_id):
    category_names = ["Cat 1", "Cat 2", "Cat 3"]  # Example category names
    price_steps = (max_price - min_price) / 2
    try:
        for i in range(3):
            cat_price = min_price + i * price_steps
            # Create a new TicketCategory object
            ticket_category = TicketCategory(
                CatName=category_names[i],
                CatPrice=cat_price,
                SeatsAvailable=100,
                EventID=event_id
            )
            db.session.add(ticket_category)

        db.session.commit()  # Commit all added categories to the database
        logging.info("Ticket categories stored successfully!")
    except Exception as e:
        db.session.rollback()  # Rollback in case of error
        logging.error(f"Failed to store ticket categories: {e}\n{traceback.format_exc()}")


def store_image(image_data, location_id, event_id):
    for image in image_data:
        try:
            image_id = db.session.query(func.max(Image.ImageID)).scalar()
            if image_id is not None:
                image_id += 1
            else:
                image_id = 1
            img = Image(
                ImageID = image_id,
                URL=image['url'],
                Ratio=image['ratio'],
                Width=image['width'],
                Height=image['height'],
                LocationID=location_id,
                EventID=event_id
            )
            db.session.add(img)
            image_id += 1
        except KeyError as e:
            logging.error(f"Key error: {e} - missing image data key in {image}")
        except Exception as e:
            logging.error(f"An error occurred while storing image data: {e}")
            db.session.rollback()
    db.session.commit()
    logging.info(f"Image id: {image_id}, LocationID: {img.LocationID}, Event id: {img.EventID}, url: {img.URL}, ratio: {img.Ratio}, Width: {img.Width},  Height: {img.Height} stored successfully!")
