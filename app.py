from flask import Flask, render_template, request, redirect, flash, session, url_for, send_file
import sqlite3
import os
import heapq
import collections
import html
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
import math
import json
from functools import lru_cache
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

# ---------------- DATABASE MANAGER CLASS (ENCAPSULATION & ABSTRACTION) ----------------
# Encapsulation: Hides database connection details and provides a clean interface
# Abstraction: Abstracts database operations into methods

class DatabaseManager:
    def __init__(self, db_path='database.db'):
        self.db_path = db_path

    def get_connection(self):
        """Encapsulated database connection method"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    def execute_query(self, query, params=(), fetch_one=False, fetch_all=False):
        """Abstracted query execution with error handling"""
        conn = self.get_connection()
        try:
            result = conn.execute(query, params)
            if fetch_one:
                return result.fetchone()
            elif fetch_all:
                return result.fetchall()
            else:
                conn.commit()
                return result.lastrowid
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

# ---------------- USER BASE CLASS (INHERITANCE & POLYMORPHISM) ----------------
# Inheritance: Base class for all user types
# Polymorphism: Different user types can have different behaviors
class User(ABC):
    def __init__(self, user_id=None, fname='', lname='', email='', role=''):
        self.user_id = user_id
        self.fname = fname
        self.lname = lname
        self.email = email
        self.role = role
        self.db = DatabaseManager()

    @abstractmethod
    def get_dashboard_data(self):
        """Polymorphic method for different dashboard data"""
        pass

    def authenticate(self, password):
        """Common authentication method"""
        user = self.db.execute_query(
            "SELECT * FROM users WHERE email=? AND password=?",
            (self.email, password), fetch_one=True
        )
        return user

    def update_profile(self, data):
        """Common profile update method"""
        # Implementation would vary by user type
        pass

# ---------------- PATIENT CLASS (INHERITS FROM USER) ----------------
class Patient(User):
    def __init__(self, user_id=None, fname='', lname='', email='', **kwargs):
        super().__init__(user_id, fname, lname, email, 'user')
        self.city = kwargs.get('city', '')
        self.state = kwargs.get('state', '')
        self.pincode = kwargs.get('pincode', '')
        self.address = kwargs.get('address', '')

    def get_dashboard_data(self):
        """Polymorphic implementation for patient dashboard"""
        now = datetime.now()
        current_date = now.strftime('%Y-%m-%d')

        # Get upcoming appointments
        upcoming = self.db.execute_query("""
            SELECT a.*, d.fname as doctor_fname, d.lname as doctor_lname, d.specialty
            FROM appointments a
            JOIN doctors d ON a.doctor_id = d.id
            WHERE a.patient_id=? AND a.status='accepted' AND
            (a.date > ? OR (a.date = ? AND a.time > ?))
            ORDER BY a.date, a.time
        """, (self.user_id, current_date, current_date, now.strftime('%H:%M')), fetch_all=True)

        # Get past appointments
        past = self.db.execute_query("""
            SELECT a.*, d.fname as doctor_fname, d.lname as doctor_lname, d.specialty
            FROM appointments a
            JOIN doctors d ON a.doctor_id = d.id
            WHERE a.patient_id=? AND
            (a.date < ? OR (a.date = ? AND a.time <= ?))
            ORDER BY a.date DESC, a.time DESC LIMIT 5
        """, (self.user_id, current_date, current_date, now.strftime('%H:%M')), fetch_all=True)

        # Get notifications
        notifications = self.db.execute_query("""
            SELECT * FROM notifications
            WHERE user_id=? AND type IN ('appointment_accepted', 'appointment_rejected')
            ORDER BY created_at DESC LIMIT 5
        """, (self.user_id,), fetch_all=True)

        # Determine greeting
        current_hour = now.hour
        greeting = "Good Morning" if current_hour < 12 else "Good Afternoon" if current_hour < 17 else "Good Evening"

        return {
            'name': self.fname,
            'greeting': greeting,
            'upcoming_appointments': upcoming,
            'past_appointments': past,
            'notifications': notifications
        }

# ---------------- DOCTOR CLASS (INHERITS FROM USER) ----------------
class Doctor(User):
    def __init__(self, user_id=None, fname='', lname='', email='', **kwargs):
        super().__init__(user_id, fname, lname, email, 'doctor')
        self.specialty = kwargs.get('specialty', '')
        self.fees = kwargs.get('fees', 0)
        self.profile_complete = kwargs.get('profile_complete', 0)
        self.profile_percent = kwargs.get('profile_percent', 25)

    def get_dashboard_data(self):
        """Polymorphic implementation for doctor dashboard"""
        today = datetime.now().strftime('%Y-%m-%d')

        # Get doctor's ID
        doctor_record = self.db.execute_query("SELECT id FROM doctors WHERE user_id=?", (self.user_id,), fetch_one=True)
        doctor_id = doctor_record['id'] if doctor_record else None

        if not doctor_id:
            return {'profile_complete': 0, 'profile_percent': 25}

        # Get today's appointments
        todays_appointments = self.db.execute_query("""
            SELECT a.*, u.fname as patient_fname, u.lname as patient_lname
            FROM appointments a
            JOIN users u ON a.patient_id = u.id
            WHERE a.doctor_id=? AND a.date=? AND a.status='accepted'
            ORDER BY a.time
        """, (doctor_id, today), fetch_all=True)

        # Get upcoming appointments
        upcoming_appointments = self.db.execute_query("""
            SELECT a.*, u.fname as patient_fname, u.lname as patient_lname
            FROM appointments a
            JOIN users u ON a.patient_id = u.id
            WHERE a.doctor_id=? AND a.date > ? AND a.status='accepted'
            ORDER BY a.date, a.time
        """, (doctor_id, today), fetch_all=True)

        # Get total patients
        total_patients = self.db.execute_query("""
            SELECT COUNT(DISTINCT patient_id) as count FROM appointments
            WHERE doctor_id=? AND status IN ('accepted', 'pending')
        """, (doctor_id,), fetch_one=True)['count']

        return {
            'name': self.fname,
            'profile_complete': self.profile_complete,
            'profile_percent': self.profile_percent,
            'todays_appointments': todays_appointments,
            'upcoming_appointments': upcoming_appointments,
            'total_patients': total_patients
        }

# ---------------- APPOINTMENT MANAGER CLASS (ENCAPSULATION) ----------------
# Encapsulation: Manages all appointment-related operations
class AppointmentManager:
    def __init__(self):
        self.db = DatabaseManager()

    def create_appointment(self, patient_id, doctor_id, date, time, address="Patient's address"):
        """Create new appointment"""
        return self.db.execute_query("""
            INSERT INTO appointments (patient_id, doctor_id, date, time, address)
            VALUES (?, ?, ?, ?, ?)
        """, (patient_id, doctor_id, date, time, address))

    def get_appointments_by_doctor(self, doctor_id, status_filter=None):
        """Get appointments for a doctor with optional status filter"""
        query = """
            SELECT a.*, u.fname as patient_fname, u.lname as patient_lname,
                   strftime('%Y-%m-%d', a.date) as date_str
            FROM appointments a
            JOIN users u ON a.patient_id = u.id
            WHERE a.doctor_id=?
        """
        params = [doctor_id]

        if status_filter:
            query += " AND a.status IN ({})".format(','.join('?' * len(status_filter)))
            params.extend(status_filter)

        query += " ORDER BY a.date, a.time"
        return self.db.execute_query(query, tuple(params), fetch_all=True)

    def update_appointment_status(self, appointment_id, status):
        """Update appointment status"""
        self.db.execute_query("UPDATE appointments SET status=? WHERE id=?", (status, appointment_id))

# ---------------- DISTANCE CALCULATOR CLASS (ABSTRACTION & ENCAPSULATION) ----------------
# Abstraction: Hides complex distance calculation logic
# Encapsulation: Contains all distance-related methods
class DistanceCalculator:
    GEOCODE_URL = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q="
    ROUTE_URL = "https://router.project-osrm.org/route/v1/driving/"
    HTTP_USER_AGENT = "HealcardGP/1.0 (distance-calculation)"

    # City center coordinates for city-to-city distance fallback (India)
    CITY_COORDINATES = {
        'ahmedabad': (23.0225, 72.5714),
        'amritsar': (31.6340, 74.8723),
        'aurangabad': (19.8762, 75.3433),
        'bengaluru': (12.9716, 77.5946),
        'bhavnagar': (21.7645, 72.1519),
        'bhopal': (23.2599, 77.4126),
        'bhubaneswar': (20.2961, 85.8245),
        'chandigarh': (30.7333, 76.7794),
        'chennai': (13.0827, 80.2707),
        'coimbatore': (11.0168, 76.9558),
        'dehradun': (30.3165, 78.0322),
        'delhi': (28.6139, 77.2090),
        'dhanbad': (23.7957, 86.4304),
        'dimapur': (25.9091, 93.7276),
        'faridabad': (28.4089, 77.3178),
        'gangtok': (27.3389, 88.6065),
        'gaya': (24.7914, 85.0002),
        'goa': (15.2993, 74.1240),
        'guwahati': (26.1445, 91.7362),
        'gurugram': (28.4595, 77.0266),
        'hyderabad': (17.3850, 78.4867),
        'imphal': (24.8170, 93.9368),
        'indore': (22.7196, 75.8577),
        'itanagar': (27.0844, 93.6053),
        'jaipur': (26.9124, 75.7873),
        'jammu': (32.7266, 74.8570),
        'jamshedpur': (22.8046, 86.2029),
        'jodhpur': (26.2389, 73.0243),
        'kanpur': (26.4499, 80.3319),
        'kochi': (9.9312, 76.2673),
        'kohima': (25.6751, 94.1086),
        'kolkata': (22.5726, 88.3639),
        'kota': (25.2138, 75.8648),
        'kozhikode': (11.2588, 75.7804),
        'lucknow': (26.8467, 80.9462),
        'ludhiana': (30.9010, 75.8573),
        'madurai': (9.9252, 78.1198),
        'mangaluru': (12.9141, 74.8560),
        'mumbai': (19.0760, 72.8777),
        'mysuru': (12.2958, 76.6394),
        'nagpur': (21.1458, 79.0882),
        'nashik': (19.9975, 73.7898),
        'new delhi': (28.6139, 77.2090),
        'noida': (28.5355, 77.3910),
        'panaji': (15.4909, 73.8278),
        'patna': (25.5941, 85.1376),
        'port blair': (11.6234, 92.7265),
        'prayagraj': (25.4358, 81.8463),
        'puducherry': (11.9416, 79.8083),
        'pune': (18.5204, 73.8567),
        'raipur': (21.2514, 81.6296),
        'rajkot': (22.3039, 70.8022),
        'ranchi': (23.3441, 85.3096),
        'siliguri': (26.7271, 88.3953),
        'shimla': (31.1048, 77.1734),
        'srinagar': (34.0837, 74.7973),
        'surat': (21.1702, 72.8311),
        'thane': (19.2183, 72.9781),
        'thiruvananthapuram': (8.5241, 76.9366),
        'thrissur': (10.5276, 76.2144),
        'udaipur': (24.5854, 73.7125),
        'vadodara': (22.3072, 73.1812),
        'varanasi': (25.3176, 82.9739),
        'vijayawada': (16.5062, 80.6480),
        'visakhapatnam': (17.6868, 83.2185),
        'warangal': (17.9689, 79.5941)
    }

    @staticmethod
    def _parse_coordinates(address):
        """Return validated (lat, lon) tuple or None."""
        try:
            lat = float(address.get('latitude'))
            lon = float(address.get('longitude'))
        except (TypeError, ValueError):
            return None

        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return (lat, lon)

    @staticmethod
    def _normalize_text(value):
        return (value or '').strip().lower()

    @staticmethod
    def _request_json(url):
        req = Request(url, headers={"User-Agent": DistanceCalculator.HTTP_USER_AGENT})
        with urlopen(req, timeout=6) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _city_coordinates(address):
        city = DistanceCalculator._normalize_text((address or {}).get('city'))
        if not city:
            return None
        city = city.split(',')[0].strip()
        return DistanceCalculator.CITY_COORDINATES.get(city)

    @staticmethod
    def _build_location_query(address):
        if not address:
            return ""
        city = (address.get('city') or '').strip()
        state = (address.get('state') or '').strip()
        if not city:
            return ""
        parts = [
            city,
            state,
            'India'
        ]
        query = ", ".join([p for p in parts if p])
        return query.strip(", ")

    @staticmethod
    @lru_cache(maxsize=1024)
    def _geocode_query(query):
        if not query:
            return None
        try:
            url = f"{DistanceCalculator.GEOCODE_URL}{quote_plus(query)}"
            payload = DistanceCalculator._request_json(url)
            if not payload:
                return None
            lat = float(payload[0].get("lat"))
            lon = float(payload[0].get("lon"))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return (lat, lon)
        except Exception:
            return None
        return None

    @staticmethod
    @lru_cache(maxsize=4096)
    def _road_distance_query(lat1, lon1, lat2, lon2):
        try:
            url = (
                f"{DistanceCalculator.ROUTE_URL}"
                f"{lon1},{lat1};{lon2},{lat2}"
                f"?overview=false&alternatives=false&steps=false"
            )
            payload = DistanceCalculator._request_json(url)
            routes = payload.get("routes", [])
            if not routes:
                return None
            meters = routes[0].get("distance")
            if meters is None:
                return None
            return float(meters) / 1000.0
        except Exception:
            return None

    @staticmethod
    def _road_distance_km(coords1, coords2):
        lat1, lon1 = round(coords1[0], 6), round(coords1[1], 6)
        lat2, lon2 = round(coords2[0], 6), round(coords2[1], 6)
        return DistanceCalculator._road_distance_query(lat1, lon1, lat2, lon2)

    @staticmethod
    def _extract_coordinates(address):
        # City-to-city mode: use only city/state to determine coordinates
        query = DistanceCalculator._build_location_query(address)
        geocoded = DistanceCalculator._geocode_query(query)
        if geocoded:
            return geocoded

        # Fallback: static city center dictionary
        return DistanceCalculator._city_coordinates(address)

    @staticmethod
    def calculate_distance(patient_addr, doctor_addr):
        """
        Calculate city-to-city distance between patient and doctor.
        Uses city/state geocoding and city-center fallback.
        """
        try:
            patient_coords = DistanceCalculator._extract_coordinates(patient_addr)
            doctor_coords = DistanceCalculator._extract_coordinates(doctor_addr)
            if not patient_coords or not doctor_coords:
                return None

            lat1, lon1 = patient_coords
            lat2, lon2 = doctor_coords
            if lat1 == lat2 and lon1 == lon2:
                return 0.0

            # Prefer real-world drivable distance
            road_km = DistanceCalculator._road_distance_km(patient_coords, doctor_coords)
            if road_km is not None:
                return road_km

            # Fallback to straight-line Haversine distance
            earth_radius_km = 6371
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)
            a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            return earth_radius_km * c

        except Exception:
            return None

# ---------------- DOCTOR SORTER CLASS (USES DATA STRUCTURES) ----------------
# Uses Heap data structure for efficient sorting by distance
class DoctorSorter:
    @staticmethod
    def sort_doctors_by_distance(doctors, patient_address):
        """
        Sort doctors by distance using min-heap (priority queue).
        Data Structure: Heap for O(n log n) sorting with distance priority
        """
        distance_heap = []
        calculator = DistanceCalculator()

        for doctor in doctors:
            doctor_dict = dict(doctor)
            doctor_addr = {
                'city': doctor['city'], 'state': doctor['state'],
                'pincode': doctor['pincode'], 'address': doctor['full_address'],
                'latitude': doctor['latitude'], 'longitude': doctor['longitude']
            }

            distance = calculator.calculate_distance(patient_address, doctor_addr)
            priority_distance = distance if distance is not None else float('inf')
            doctor_dict['distance'] = priority_distance
            doctor_dict['distance_display'] = f"{distance:.1f} km" if distance is not None else "N/A"

            # Push to heap: (priority, tie_breaker, data)
            heapq.heappush(distance_heap, (priority_distance, doctor_dict['id'], doctor_dict))

        # Extract sorted list
        sorted_doctors = []
        while distance_heap:
            _, _, doctor_dict = heapq.heappop(distance_heap)
            sorted_doctors.append(doctor_dict)

        return sorted_doctors

# ---------------- PAYMENT MANAGER CLASS (ENCAPSULATION) ----------------
class PaymentManager:
    def __init__(self):
        self.db = DatabaseManager()

    def process_payment(self, appointment_id, user_id, amount, payment_method, payment_details=""):
        """Process payment and record transaction"""
        self.db.execute_query("""
            INSERT INTO payments (appointment_id, user_id, amount, payment_method, payment_details, status)
            VALUES (?, ?, ?, ?, ?, 'completed')
        """, (appointment_id, user_id, amount, payment_method, payment_details))

    def deduct_wallet_balance(self, user_id, amount):
        """Deduct from wallet balance"""
        self.db.execute_query("UPDATE wallets SET balance = balance - ? WHERE user_id=?", (amount, user_id))

    def add_wallet_balance(self, user_id, amount):
        """Add to wallet balance"""
        self.db.execute_query("UPDATE wallets SET balance = balance + ? WHERE user_id=?", (amount, user_id))

# ---------------- NOTIFICATION MANAGER CLASS (ENCAPSULATION) ----------------
class NotificationManager:
    def __init__(self):
        self.db = DatabaseManager()

    def create_notification(self, user_id, message, notification_type, appointment_id=None):
        """Create new notification"""
        self.db.execute_query("""
            INSERT INTO notifications (user_id, message, type, appointment_id)
            VALUES (?, ?, ?, ?)
        """, (user_id, message, notification_type, appointment_id))

    def get_notifications(self, user_id, notification_type=None):
        """Get notifications for user"""
        query = "SELECT * FROM notifications WHERE user_id=?"
        params = [user_id]

        if notification_type:
            query += " AND type=?"
            params.append(notification_type)

        query += " ORDER BY created_at DESC"
        return self.db.execute_query(query, tuple(params), fetch_all=True)

# ---------------- GLOBAL FLASK APP INSTANCE ----------------
app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Should be from config


@app.after_request
def add_no_cache_headers(response):
    # Prevent browser cache/back-forward cache from serving protected pages after logout.
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# ---------------- UTILITY FUNCTIONS ----------------
def get_db():
    """Get database connection"""
    db_manager = DatabaseManager()
    return db_manager.get_connection()

def get_specialty_fees(specialty):
    """Get consultation fees based on specialty (Data Structure: Dictionary)"""
    specialty_fees = {
        'General Physician': 200, 'Gynaecology': 500, 'Dermatology': 500,
        'Gastrology': 200, 'Psychiatry': 200, 'Child Care': 500,
        'Urology': 500, 'Cold & Fever': 500
    }
    return specialty_fees.get(specialty, 0)

# ---------------- HOME ----------------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/static-page")
def static_page():
    """Standalone responsive static page."""
    return render_template("static_page.html")

# ---------------- VALIDATION FUNCTIONS ----------------
import re

def validate_name(name, field_name):
    """Validate that name contains only characters (a-z, A-Z)"""
    if name and not re.match(r'^[a-zA-Z]+$', name):
        return f"{field_name} should contain only letters (a-z, A-Z)"
    return None

def validate_city_state(value, field_name):
    """Validate that city/state contains only characters"""
    if value and not re.match(r'^[a-zA-Z\s]+$', value):
        return f"{field_name} should contain only letters"
    return None

def validate_digits_only(value, field_name):
    """Validate that field contains only digits"""
    if value and not re.match(r'^\d+$', str(value)):
        return f"{field_name} should contain only digits"
    return None

def validate_no_digits(value, field_name):
    """Validate that field does not contain any digits"""
    if value and re.search(r'\d', str(value)):
        return f"{field_name} should not contain digits"
    return None

def validate_experience_vs_age(experience, age):
    """Validate that experience is not greater than (age - 18)"""
    if experience and age:
        try:
            exp = int(experience)
            age_val = int(age)
            if exp > (age_val - 18):
                return f"Experience ({exp} years) cannot be greater than age ({age_val}) minus 18 years"
        except (ValueError, TypeError):
            pass
    return None

def validate_emergency_contact(contact):
    """Validate emergency contact is exactly 4 digits"""
    if contact and not re.match(r'^\d{4}$', str(contact)):
        return "Emergency contact must be exactly 4 digits"
    return None

# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        conn = None
        try:
            conn = get_db()

            fname = request.form["fname"]
            mname = request.form.get("mname")
            lname = request.form["lname"]
            gender = request.form["gender"]
            dob = request.form.get("dob")
            age = request.form.get("age")
            email = request.form["email"].strip().lower()
            mobile = request.form["mobile"].strip()
            password = request.form["password"]
            role = request.form["role"]

            patient_city = request.form.get("patient_city")
            patient_state = request.form.get("patient_state")
            patient_pincode = request.form.get("patient_pincode")
            patient_address = request.form.get("patient_address")

            # Server-side validation for name fields
            errors = []
            
            fname_error = validate_name(fname, "First name")
            if fname_error:
                errors.append(fname_error)
            
            if mname:
                mname_error = validate_name(mname, "Middle name")
                if mname_error:
                    errors.append(mname_error)
            
            lname_error = validate_name(lname, "Last name")
            if lname_error:
                errors.append(lname_error)

            # Validate city and state (patient profile)
            if patient_city:
                city_error = validate_city_state(patient_city, "City")
                if city_error:
                    errors.append(city_error)
            
            if patient_state:
                state_error = validate_city_state(patient_state, "State")
                if state_error:
                    errors.append(state_error)

            if errors:
                for error in errors:
                    flash(error, "danger")
                return redirect("/register")

            if not re.match(r'^[6-9][0-9]{9}$', mobile):
                flash("Please enter a valid 10-digit mobile number.", "danger")
                return redirect("/register")

            existing_email = conn.execute(
                "SELECT id FROM users WHERE lower(email)=?",
                (email,)
            ).fetchone()
            if existing_email:
                flash("Email already registered.", "danger")
                return redirect("/register")

            existing_mobile = conn.execute(
                "SELECT id FROM users WHERE mobile=?",
                (mobile,)
            ).fetchone()
            if existing_mobile:
                flash("Mobile number already registered.", "danger")
                return redirect("/register")

            cur = conn.execute("""
                INSERT INTO users
                (fname, mname, lname, dob, age, email, mobile, password, role,
                 patient_city, patient_state, patient_pincode, patient_address)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                fname, mname, lname, dob, age,
                email, mobile, password, role,
                patient_city, patient_state, patient_pincode, patient_address
            ))

            user_id = cur.lastrowid

            # wallet for all users
            conn.execute("""
                INSERT INTO wallets (user_id, balance)
                VALUES (?, 0.0)
            """, (user_id,))

            # doctor default profile
            if role == "doctor":
                conn.execute("""
                    INSERT INTO doctors
                    (user_id, fname, lname, specialty, fees, profile_complete, profile_percent)
                    VALUES (?, ?, ?, 'Not specified', 0, 0, 25)
                """, (user_id, fname, lname))

            conn.commit()
            return redirect("/login")

        except sqlite3.IntegrityError as e:
            if conn:
                conn.rollback()

            if "users.email" in str(e):
                flash("Email already registered.", "danger")
            elif "users.mobile" in str(e):
                flash("Mobile number already registered.", "danger")
            else:
                flash("Registration failed.", "danger")

            return redirect("/register")

        finally:
            if conn:
                conn.close()

    return render_template("register.html")


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE email=? AND password=?",
            (email, password)
        ).fetchone()
        conn.close()

        if user:
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["name"] = user["fname"]

            if user["role"] == "doctor":
                return redirect("/doctor/dashboard")
            else:
                return redirect("/dashboard")

        flash("Invalid Email or Password", "danger")
        return redirect("/login")

    return render_template("login.html")

# ---------------- USER DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()

    # Get current date and time for filtering
    from datetime import datetime
    now = datetime.now()
    current_date = now.strftime('%Y-%m-%d')
    current_datetime = now.strftime('%Y-%m-%d %H:%M:%S')

    # Get upcoming accepted appointments (future dates or today but future time)
    upcoming_appointments = conn.execute("""
        SELECT a.*, d.fname as doctor_fname, d.lname as doctor_lname, d.specialty
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.patient_id=? AND a.status='accepted' AND
        (a.date > ? OR (a.date = ? AND a.time > ?))
        ORDER BY a.date, a.time
    """, (session["user_id"], current_date, current_date, now.strftime('%H:%M'))).fetchall()

    # Get past appointments (past dates or today but past time)
    past_appointments = conn.execute("""
        SELECT a.*, d.fname as doctor_fname, d.lname as doctor_lname, d.specialty
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.patient_id=? AND
        (a.date < ? OR (a.date = ? AND a.time <= ?))
        ORDER BY a.date DESC, a.time DESC
        LIMIT 5
    """, (session["user_id"], current_date, current_date, now.strftime('%H:%M'))).fetchall()

    # Get patient notifications
    notifications = conn.execute("""
        SELECT * FROM notifications
        WHERE user_id=? AND type IN ('appointment_accepted', 'appointment_rejected')
        ORDER BY created_at DESC
        LIMIT 5
    """, (session["user_id"],)).fetchall()

    # Get user profile completeness
    user = conn.execute("SELECT profile_percent FROM users WHERE id=?", (session["user_id"],)).fetchone()
    profile_percent = user["profile_percent"] if user else 0

    # Unread notifications count for navbar badge
    notifications_count = conn.execute(
        "SELECT COUNT(*) as count FROM notifications WHERE user_id=? AND read=0",
        (session["user_id"],)
    ).fetchone()["count"]

    conn.close()

    # Determine time-based greeting
    current_hour = now.hour
    if current_hour < 12:
        greeting = "Good Morning"
    elif current_hour < 17:
        greeting = "Good Afternoon"
    else:
        greeting = "Good Evening"

    return render_template("dashboard.html",
                         name=session.get("name"),
                         greeting=greeting,
                         upcoming_appointments=upcoming_appointments,
                         past_appointments=past_appointments,
                         notifications=notifications,
                         profile_percent=profile_percent,
                         notifications_count=notifications_count)

# ---------------- APPOINTMENTS PAGE ----------------
@app.route("/appointments")
def appointments():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()

    # Get today's date
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')

    # Get today's appointments with complete doctor info
    todays_appointments = conn.execute("""
        SELECT a.id, a.date, a.time, a.status, a.doctor_id, a.patient_id,
               d.id as doc_id, d.fname as doctor_fname, d.lname as doctor_lname, 
               d.specialty, d.address, d.fees,
               u.fname as patient_fname, u.lname as patient_lname
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        JOIN users u ON a.patient_id = u.id
        WHERE a.patient_id=? AND a.status='accepted' AND a.date = ?
        ORDER BY a.time
    """, (session["user_id"], today)).fetchall()

    # Get future appointments (after today) with complete doctor info
    future_appointments = conn.execute("""
        SELECT a.id, a.date, a.time, a.status, a.doctor_id, a.patient_id,
               d.id as doc_id, d.fname as doctor_fname, d.lname as doctor_lname, 
               d.specialty, d.address, d.fees,
               u.fname as patient_fname, u.lname as patient_lname
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        JOIN users u ON a.patient_id = u.id
        WHERE a.patient_id=? AND a.status='accepted' AND a.date > ?
        ORDER BY a.date, a.time
    """, (session["user_id"], today)).fetchall()

    # Get pending appointments (awaiting confirmation)
    pending_appointments = conn.execute("""
        SELECT a.id, a.date, a.time, a.status, a.doctor_id, a.patient_id,
               d.id as doc_id, d.fname as doctor_fname, d.lname as doctor_lname, 
               d.specialty, d.address, d.fees,
               u.fname as patient_fname, u.lname as patient_lname
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        JOIN users u ON a.patient_id = u.id
        WHERE a.patient_id=? AND a.status='pending'
        ORDER BY a.date, a.time
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template("appointments.html",
                         name=session.get("name"),
                         todays_appointments=todays_appointments,
                         future_appointments=future_appointments,
                         pending_appointments=pending_appointments)

# ---------------- CANCEL APPOINTMENT ----------------
@app.route("/cancel_appointment/<int:appointment_id>", methods=["POST"])
def cancel_appointment(appointment_id):
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()
    
    try:
        # Verify the appointment belongs to the current user
        appointment = conn.execute(
            "SELECT patient_id, status FROM appointments WHERE id=?", 
            (appointment_id,)
        ).fetchone()
        
        if not appointment:
            return {"error": "Appointment not found"}, 404
        
        if appointment["patient_id"] != session["user_id"]:
            return {"error": "Unauthorized"}, 403
        
        # Only allow cancellation if status is 'pending' or 'accepted'
        if appointment["status"] not in ['pending', 'accepted']:
            return {"error": "Cannot cancel this appointment"}, 400
        
        # Update appointment status to 'cancelled'
        conn.execute(
            "UPDATE appointments SET status='cancelled' WHERE id=?",
            (appointment_id,)
        )
        
        # If there was a payment, refund it
        payment = conn.execute(
            "SELECT * FROM payments WHERE appointment_id=? AND status='completed'",
            (appointment_id,)
        ).fetchone()
        
        if payment:
            # Refund the amount to wallet
            conn.execute(
                "UPDATE wallets SET balance = balance + ? WHERE user_id=?",
                (payment["amount"], session["user_id"])
            )
            
            # Record refund transaction
            conn.execute(
                "INSERT INTO payments (user_id, amount, appointment_id, payment_method, status) VALUES (?, ?, ?, ?, ?)",
                (session["user_id"], payment["amount"], appointment_id, payment["payment_method"], 'refunded')
            )
        
        conn.commit()
        flash(f"Appointment cancelled successfully! Refunded amount: ₹{payment['amount'] if payment else '0':.2f}", "success")
        return {"success": True}, 200
        
    except Exception as e:
        conn.rollback()
        print(f"Error cancelling appointment: {e}")
        return {"error": str(e)}, 500
    finally:
        conn.close()

# ---------------- DOCTOR DASHBOARD ----------------
@app.route("/doctor/dashboard")
def doctor_dashboard():
    if "user_id" not in session or session.get("role") != "doctor":
        return redirect("/login")

    conn = get_db()
    doctor = conn.execute("""
        SELECT profile_complete, profile_percent
        FROM doctors WHERE user_id=?
    """, (session["user_id"],)).fetchone()

    if not doctor:
        # If doctor profile not found, create default values
        profile_complete = 0
        profile_percent = 25
    else:
        profile_complete = doctor["profile_complete"]
        profile_percent = doctor["profile_percent"]

    # Get doctor's ID
    doctor_id = conn.execute("SELECT id FROM doctors WHERE user_id=?", (session["user_id"],)).fetchone()["id"]

    # Get today's date
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')

    # Get today's appointments list
    todays_appointments = conn.execute("""
        SELECT a.*, u.fname as patient_fname, u.lname as patient_lname
        FROM appointments a
        JOIN users u ON a.patient_id = u.id
        WHERE a.doctor_id=? AND a.date=? AND a.status='accepted'
        ORDER BY a.time
    """, (doctor_id, today)).fetchall()

    # Get upcoming appointments (future dates)
    upcoming_appointments = conn.execute("""
        SELECT a.*, u.fname as patient_fname, u.lname as patient_lname
        FROM appointments a
        JOIN users u ON a.patient_id = u.id
        WHERE a.doctor_id=? AND a.date > ? AND a.status='accepted'
        ORDER BY a.date, a.time
    """, (doctor_id, today)).fetchall()

    # Get total patients count
    total_patients = conn.execute("""
        SELECT COUNT(DISTINCT patient_id) as count FROM appointments
        WHERE doctor_id=? AND status='accepted'
    """, (doctor_id,)).fetchone()["count"]

    # Get unread notifications count
    notifications_count = conn.execute("SELECT COUNT(*) as count FROM notifications WHERE user_id=? AND read=0", (session["user_id"],)).fetchone()["count"]

    # Get wallet balance
    wallet = conn.execute("SELECT balance FROM wallets WHERE user_id=?", (session["user_id"],)).fetchone()
    wallet_balance = wallet["balance"] if wallet else 0.0

    conn.close()

    return render_template(
        "doctor_dashboard.html",
        name=session.get("name"),
        profile_complete=profile_complete,
        profile_percent=profile_percent,
        todays_appointments=todays_appointments,
        upcoming_appointments=upcoming_appointments,
        total_patients=total_patients,
        wallet_balance=wallet_balance,
        prescriptions=0,  # Placeholder for now
        notifications_count=notifications_count
    )


@app.route("/api/wallet_balance")
def api_wallet_balance():
    if "user_id" not in session:
        return {"success": False, "message": "Unauthorized"}, 401

    conn = get_db()
    wallet = conn.execute("SELECT balance FROM wallets WHERE user_id=?", (session["user_id"],)).fetchone()
    conn.close()

    return {
        "success": True,
        "balance": float(wallet["balance"]) if wallet else 0.0
    }

# ---------------- DOCTOR APPOINTMENTS ----------------
@app.route("/doctor/appointments")
def doctor_appointments():
    if "user_id" not in session or session.get("role") != "doctor":
        return redirect("/login")

    conn = get_db()

    # Get doctor's ID
    doctor_id = conn.execute("SELECT id FROM doctors WHERE user_id=?", (session["user_id"],)).fetchone()["id"]

    # Get today's date
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    today_obj = datetime.strptime(today, '%Y-%m-%d')
    today_display = today_obj.strftime('%d %b %Y')

    # Get all accepted and pending appointments grouped by date
    appointments_by_date = conn.execute("""
        SELECT a.*, u.fname as patient_fname, u.lname as patient_lname,
               strftime('%Y-%m-%d', a.date) as date_str
        FROM appointments a
        JOIN users u ON a.patient_id = u.id
        WHERE a.doctor_id=? AND a.status IN ('accepted', 'pending')
        ORDER BY a.date, a.time
    """, (doctor_id,)).fetchall()

    # Debug: Print today's date and appointment dates
    print(f"DEBUG: Today's date: {today}")
    for apt in appointments_by_date:
        print(f"DEBUG: Appointment date: {apt['date_str']}, Original date: {apt['date']}")

    # Build grouped_appointments
    grouped_appointments = {}

    for appointment in appointments_by_date:
        date = appointment['date_str']
        # Format date for display
        try:
            date_obj = datetime.strptime(date, '%Y-%m-%d')
            formatted_date = date_obj.strftime('%d %b %Y')
        except:
            formatted_date = date

        if formatted_date not in grouped_appointments:
            grouped_appointments[formatted_date] = {'date_key': date, 'appointments': []}
        grouped_appointments[formatted_date]['appointments'].append(dict(appointment))

    conn.close()

    return render_template(
        "doctor_appointments.html",
        name=session.get("name"),
        grouped_appointments=grouped_appointments,
        today=today,
        today_display=today_display
    )

# ---------------- DOCTOR PROFILE (COMPLETE PROFILE PAGE) ----------------
@app.route("/doctor/profile", methods=["GET", "POST"])
def doctor_profile():
    if 'user_id' not in session or session.get('role') != 'doctor':
        return redirect("/login")

    conn = get_db()
    doctor = conn.execute(
        "SELECT * FROM doctors WHERE user_id = ?",
        (session["user_id"],)
    ).fetchone()

    if not doctor:
        conn.close()
        flash("Doctor profile not found", "danger")
        return redirect("/doctor/dashboard")

    # Get age from users table (stored at registration time)
    user = conn.execute(
        "SELECT age FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()
    
    # Convert doctor to dict and add age from users table if not present in doctors table
    doctor_dict = dict(doctor)
    if not doctor_dict.get('age') and user and user['age']:
        doctor_dict['age'] = user['age']
    
    doctor = doctor_dict

    if request.method == "POST":

        specialty = request.form.get("specialty")
        age = request.form.get("age")
        experience_years = request.form.get("experience_years")
        qualification = request.form.get("qualification")
        license_no = request.form.get("license")
        hospital_name = request.form.get("hospital_name")
        city = request.form.get("city")
        state = request.form.get("state")
        landmark = request.form.get("landmark")
        full_address = request.form.get("full_address")
        pincode = request.form.get("pincode")
        latitude = request.form.get("latitude")
        longitude = request.form.get("longitude")
        staff_count = request.form.get("staff_count")
        languages = request.form.get("languages")
        reviews = request.form.get("reviews")
        awards = request.form.get("awards")
        emergency_contact = request.form.get("emergency_contact")

        # Server-side validation for doctor profile fields
        errors = []

        # Validate experience vs age (experience <= age - 18)
        if experience_years and age:
            exp_error = validate_experience_vs_age(experience_years, age)
            if exp_error:
                errors.append(exp_error)

        # Validate license number (digits only)
        if license_no:
            license_error = validate_digits_only(license_no, "Medical license number")
            if license_error:
                errors.append(license_error)

        # Validate city (characters only)
        if city:
            city_error = validate_city_state(city, "City")
            if city_error:
                errors.append(city_error)

        # Validate state (characters only)
        if state:
            state_error = validate_city_state(state, "State")
            if state_error:
                errors.append(state_error)

        # Validate languages (no digits)
        if languages:
            lang_error = validate_no_digits(languages, "Languages")
            if lang_error:
                errors.append(lang_error)

        # Validate awards (no digits)
        if awards:
            awards_error = validate_no_digits(awards, "Awards")
            if awards_error:
                errors.append(awards_error)

        # Validate emergency contact (exactly 4 digits)
        if emergency_contact:
            emergency_error = validate_emergency_contact(emergency_contact)
            if emergency_error:
                errors.append(emergency_error)

        # If there are validation errors, flash them and return
        if errors:
            for error in errors:
                flash(error, "danger")
            conn.close()
            return render_template(
                "doctor_complete_profile.html",
                doctor=doctor,
                profile_percent=doctor["profile_percent"]
            )

        # Calculate profile completion percentage
        percent = 25  # Base percentage
        if specialty: percent += 7  # Specialty is required
        # Fees are now auto-calculated, so always count as complete
        percent += 7  # Fees is required (auto-calculated)
        if age: percent += 3
        if experience_years: percent += 3
        if qualification: percent += 7
        if license_no: percent += 7
        if hospital_name: percent += 4
        if city: percent += 4
        if state: percent += 4
        if landmark: percent += 3
        if full_address: percent += 7
        if pincode: percent += 4
        if latitude: percent += 3
        if longitude: percent += 3
        if staff_count: percent += 3
        if languages: percent += 3
        if reviews: percent += 6
        if awards: percent += 6
        if emergency_contact: percent += 5

        profile_complete = 1 if percent >= 100 else 0

        # Calculate fees based on specialty
        calculated_fees = get_specialty_fees(specialty) if specialty else 0

        conn.execute("""
            UPDATE doctors SET
                specialty=?, age=?, experience_years=?, qualification=?, license=?,
                hospital_name=?, city=?, state=?, landmark=?, full_address=?,
                pincode=?, latitude=?, longitude=?, staff_count=?, languages=?,
                reviews=?, awards=?, emergency_contact=?, fees=?,
                profile_percent=?, profile_complete=?
            WHERE user_id=?
        """, (
            specialty, age, experience_years, qualification, license_no,
            hospital_name, city, state, landmark, full_address,
            pincode, latitude, longitude, staff_count, languages,
            reviews, awards, emergency_contact, calculated_fees,
            percent, profile_complete,
            session["user_id"]
        ))
        conn.commit()
        conn.close()
        flash("Profile updated successfully", "success")
        return redirect("/doctor/dashboard")

    conn.close()
    return render_template(
        "doctor_complete_profile.html",
        doctor=doctor,
        profile_percent=doctor["profile_percent"]
    )

# ---------------- PATIENT PROFILE ----------------
@app.route("/profile")
def patient_profile():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()

    if not user:
        flash("User profile not found", "danger")
        return redirect("/dashboard")

    return render_template("patient_profile.html", user=user)

# ---------------- EDIT PATIENT PROFILE ----------------
@app.route("/profile/edit", methods=["GET", "POST"])
def edit_patient_profile():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

    if request.method == "POST":
        # Update patient address information
        patient_city = request.form.get("patient_city")
        patient_state = request.form.get("patient_state")
        patient_pincode = request.form.get("patient_pincode")
        patient_address = request.form.get("patient_address")
        patient_latitude = request.form.get("patient_latitude")
        patient_longitude = request.form.get("patient_longitude")

        # Calculate profile completion percentage
        percent = 33  # Base percentage from registration
        if patient_city: percent += 17  # City is required
        if patient_state: percent += 17  # State is required
        if patient_pincode: percent += 17  # Pincode is required
        if patient_address: percent += 16  # Address is required

        percent = min(percent, 100)  # Cap at 100%

        conn.execute("""
            UPDATE users SET
                patient_city=?, patient_state=?, patient_pincode=?,
                patient_address=?, patient_latitude=?, patient_longitude=?,
                profile_percent=?
            WHERE id=?
        """, (
            patient_city, patient_state, patient_pincode,
            patient_address, patient_latitude, patient_longitude,
            percent,
            session["user_id"]
        ))

        conn.commit()
        conn.close()

        flash("Profile updated successfully!", "success")
        return redirect("/profile")

    conn.close()
    return render_template("edit_patient_profile.html", user=user)

# ---------------- PATIENT APPOINTMENT HISTORY ----------------
@app.route("/history")
def appointment_history():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()

    # Get all appointments for the patient with doctor details
    appointments = conn.execute("""
        SELECT a.*, d.fname as doctor_fname, d.lname as doctor_lname, d.specialty,
               d.qualification, d.hospital_name, d.city, d.state, p.amount, p.payment_method, p.status as payment_status
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        LEFT JOIN payments p ON a.id = p.appointment_id
        WHERE a.patient_id=?
        ORDER BY d.fname, d.lname, a.date DESC, a.time DESC
    """, (session["user_id"],)).fetchall()

    # Group appointments by doctor
    doctors_appointments = {}
    for appointment in appointments:
        doctor_key = f"{appointment['doctor_fname']} {appointment['doctor_lname']}"
        if doctor_key not in doctors_appointments:
            doctors_appointments[doctor_key] = {
                'doctor_info': {
                    'fname': appointment['doctor_fname'],
                    'lname': appointment['doctor_lname'],
                    'specialty': appointment['specialty'],
                    'qualification': appointment['qualification'],
                    'hospital_name': appointment['hospital_name'],
                    'city': appointment['city'],
                    'state': appointment['state']
                },
                'appointments': []
            }
        doctors_appointments[doctor_key]['appointments'].append(dict(appointment))

    conn.close()

    return render_template("history.html", doctors_appointments=doctors_appointments)

# ----forgot-password------

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')

        # TEMP LOGIC (replace later with DB check)
        if email:
            flash(
                "If this email exists, password recovery instructions have been sent.",
                "success"
            )
            return redirect(url_for('login'))
        else:
            flash("Please enter a valid email.", "danger")

    return render_template('forgot_password.html')
# ---------------- PUBLIC DOCTOR PROFILE ----------------
@app.route("/doctor/<int:doctor_id>")
def doctor_profile_public(doctor_id):
    conn = get_db()
    doctor = conn.execute("""
        SELECT * FROM doctors
        WHERE id=? AND profile_complete=1
    """, (doctor_id,)).fetchone()

    # Get patient address and calculate distance if logged in
    patient_address = ""
    distance = None
    if "user_id" in session and session.get("role") == "user":
        patient = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if patient:
            # Build full address from patient profile
            address_parts = []
            if patient['patient_address']:
                address_parts.append(patient['patient_address'])
            if patient['patient_city']:
                address_parts.append(patient['patient_city'])
            if patient['patient_state']:
                address_parts.append(patient['patient_state'])
            if patient['patient_pincode']:
                address_parts.append(patient['patient_pincode'])
            patient_address = ", ".join(address_parts)

            # Calculate distance from patient to doctor's hospital
            if doctor:
                doctor_addr = {
                    'city': doctor['city'],
                    'state': doctor['state'],
                    'pincode': doctor['pincode'],
                    'address': doctor['full_address'],
                    'latitude': doctor['latitude'],
                    'longitude': doctor['longitude']
                }

                patient_addr = {
                    'city': patient['patient_city'],
                    'state': patient['patient_state'],
                    'pincode': patient['patient_pincode'],
                    'address': patient['patient_address'],
                    'latitude': patient['patient_latitude'],
                    'longitude': patient['patient_longitude']
                }

                calculated_distance = DistanceCalculator.calculate_distance(patient_addr, doctor_addr)
                distance = f"{calculated_distance:.1f} km" if calculated_distance is not None else "N/A"

    if not doctor:
        conn.close()
        return "Doctor profile incomplete or not found", 404


    doctor = dict(doctor)
    doctor["fees"] = get_specialty_fees(doctor["specialty"])

    conn.close()

    return render_template("doctor_profile.html", doctor=doctor, patient_address=patient_address, distance=distance)

# ---------------- BOOK SLOTS ----------------
@app.route("/book_slots/<int:doctor_id>")
def book_slots(doctor_id):
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()



    doctor = conn.execute("SELECT * FROM doctors WHERE id=?", (doctor_id,)).fetchone()

    if not doctor:
        conn.close()
        return "Doctor not found", 404

    doctor = dict(doctor)
    doctor["fees"] = get_specialty_fees(doctor["specialty"])

    # Get wallet balance
    wallet = conn.execute("SELECT balance FROM wallets WHERE user_id=?", (session["user_id"],)).fetchone()
    balance = wallet["balance"] if wallet else 0.0

    # Get all booked slots for the doctor
    booked_slots = conn.execute("""
        SELECT date, time FROM appointments
        WHERE doctor_id=? AND status IN ('accepted','pending')
    """, (doctor_id,)).fetchall()

    # Group booked slots by date
    booked_slots_dict = {}
    for slot in booked_slots:
        date = slot['date']
        time = slot['time']
        if date not in booked_slots_dict:
            booked_slots_dict[date] = []
        booked_slots_dict[date].append(time)

    conn.close()

    dates = []
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    now = datetime.now()
    for i in range(7):
        date_obj = now + timedelta(days=i)
        date_str = date_obj.strftime('%Y-%m-%d')
        day = date_obj.strftime('%d')
        month = months[date_obj.month - 1]
        dates.append({'date': date_str, 'display': f"{day} {month}"})
    return render_template("book_slots.html", doctor=doctor, dates=dates, balance=balance, booked_slots=booked_slots_dict)

# ---------------- DOCTOR LIST (CONSULT NOW) ----------------
@app.route("/doctors/<specialty>")
def doctors_by_specialty(specialty):
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()

    # Check patient profile completeness
    user = conn.execute("SELECT profile_percent FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not user or user["profile_percent"] < 100:
        conn.close()
        flash("Please complete your profile (city, state, pincode, and address) before consulting a doctor.", "warning")
        return redirect("/profile/edit")

    # Get all doctors of the specialty
    doctors = conn.execute("""
        SELECT * FROM doctors
        WHERE LOWER(specialty)=LOWER(?)
        AND profile_complete=1
    """, (specialty,)).fetchall()

    # Get patient address for sorting and distance calculation
    patient_address = None
    if "user_id" in session and session.get("role") == "user":
        patient = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if patient:
            patient_address = {
                'city': patient['patient_city'],
                'state': patient['patient_state'],
                'pincode': patient['patient_pincode'],
                'address': patient['patient_address'],
                'latitude': patient['patient_latitude'],
                'longitude': patient['patient_longitude']
            }

    # Sort doctors by distance using priority queue (min-heap for closest distance first)
    distance_heap = []
    for doctor in doctors:
        doctor_dict = dict(doctor)
        # Calculate distance if patient address available
        if patient_address:
            doctor_addr = {
                'city': doctor['city'],
                'state': doctor['state'],
                'pincode': doctor['pincode'],
                'address': doctor['full_address'],
                'latitude': doctor['latitude'],
                'longitude': doctor['longitude']
            }
            distance = DistanceCalculator.calculate_distance(patient_address, doctor_addr)
            priority_distance = distance if distance is not None else float('inf')
            doctor_dict['distance'] = priority_distance
            doctor_dict['distance_display'] = f"{distance:.1f} km" if distance is not None else "N/A"
        else:
            doctor_dict['distance'] = float('inf')
            doctor_dict['distance_display'] = "N/A"
        # Push to heap with distance as priority (min-heap for closest first), using doctor id as tie-breaker
        heapq.heappush(distance_heap, (doctor_dict['distance'], doctor_dict['id'], doctor_dict))

    # Extract from heap to get sorted list (closest distance first)
    doctors_with_distance = []
    while distance_heap:
        _, _, doctor_dict = heapq.heappop(distance_heap)
        doctors_with_distance.append(doctor_dict)

    conn.close()

    return render_template(
        "doctor_list.html",
        doctors=doctors_with_distance,
        specialty=specialty.title()
    )

# ---------------- ALL DOCTORS LIST (SORTED BY DISTANCE) ----------------
@app.route("/doctors")
def all_doctors():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()

    # Get all doctors
    doctors = conn.execute("""
        SELECT * FROM doctors
        WHERE profile_complete=1
    """).fetchall()

    # Get patient address for sorting and distance calculation
    patient_address = None
    patient = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if patient:
        patient_address = {
            'city': patient['patient_city'],
            'state': patient['patient_state'],
            'pincode': patient['patient_pincode'],
            'address': patient['patient_address'],
            'latitude': patient['patient_latitude'],
            'longitude': patient['patient_longitude']
        }

    # Sort doctors by distance using priority queue (min-heap for closest distance first)
    distance_heap = []
    for doctor in doctors:
        doctor_dict = dict(doctor)
        # Calculate distance if patient address available
        if patient_address:
            doctor_addr = {
                'city': doctor['city'],
                'state': doctor['state'],
                'pincode': doctor['pincode'],
                'address': doctor['full_address'],
                'latitude': doctor['latitude'],
                'longitude': doctor['longitude']
            }
            distance = DistanceCalculator.calculate_distance(patient_address, doctor_addr)
            priority_distance = distance if distance is not None else float('inf')
            doctor_dict['distance'] = priority_distance
            doctor_dict['distance_display'] = f"{distance:.1f} km" if distance is not None else "N/A"
        else:
            doctor_dict['distance'] = float('inf')
            doctor_dict['distance_display'] = "N/A"
        # Push to heap with distance as priority (min-heap for closest first), using doctor id as tie-breaker
        heapq.heappush(distance_heap, (doctor_dict['distance'], doctor_dict['id'], doctor_dict))

    # Extract from heap to get sorted list (closest distance first)
    doctors_with_distance = []
    while distance_heap:
        _, _, doctor_dict = heapq.heappop(distance_heap)
        doctors_with_distance.append(doctor_dict)

    conn.close()

    return render_template(
        "doctor_list.html",
        doctors=doctors_with_distance,
        specialty="All"
    )

# ---------------- DOCTOR NOTIFICATIONS ----------------
@app.route("/doctor/notifications")
def doctor_notifications():
    if "user_id" not in session or session.get("role") != "doctor":
        return redirect("/login")

    conn = get_db()
    # Mark doctor's notifications as seen when page is opened
    conn.execute("""
        UPDATE notifications
        SET read=1
        WHERE user_id=? AND type='appointment_request' AND read=0
    """, (session["user_id"],))

    notifications = conn.execute("""
        SELECT n.*, a.id as appointment_id, a.patient_id, a.doctor_id, a.date, a.time, a.address, a.status
        FROM notifications n
        LEFT JOIN appointments a ON n.appointment_id = a.id
        WHERE n.user_id=? AND n.type='appointment_request'
        ORDER BY n.created_at DESC
    """, (session["user_id"],)).fetchall()
    conn.commit()
    conn.close()

    # Get today's date for date input validation
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')

    return render_template("notifications.html", notifications=notifications, today=today)

# ---------------- PAYMENT PAGE ----------------
@app.route("/payment", methods=["GET", "POST"])
def payment():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    if request.method == "POST":
        # Handle multiple slots from new booking system (PRG pattern)
        doctor_id = request.form.get("doctor_id")
        slot_count = int(request.form.get("slot_count", 0))

        if not doctor_id or slot_count == 0:
            flash("Invalid appointment details.", "danger")
            return redirect("/dashboard")

        selected_slots = []
        for i in range(slot_count):
            date = request.form.get(f"date_{i}")
            time = request.form.get(f"time_{i}")
            if date and time:
                selected_slots.append({"date": date, "time": time})

        if not selected_slots:
            flash("No slots selected.", "danger")
            return redirect("/dashboard")

        # Store for GET render to avoid browser resubmit popup on refresh
        session['payment_data'] = {
            'doctor_id': doctor_id,
            'slot_count': len(selected_slots),
            'slots': selected_slots,
            'payment_method': 'wallet'
        }
        session['wallet_back_url'] = '/payment'
        return redirect("/payment")

    # Check if we have stored payment data from insufficient balance flow
    if 'payment_data' in session:
        payment_data = session['payment_data']  # don't pop, keep for multiple visits
        doctor_id = payment_data['doctor_id']
        slot_count = payment_data['slot_count']
        selected_slots = payment_data['slots']
        payment_method = payment_data.get('payment_method', 'wallet')
        back_url = f"/book_slots/{doctor_id}"  # Set back URL for stored payment data
    else:
        # Handle old single slot format (backward compatibility)
        doctor_id = request.args.get("doctor_id")
        selected_date = request.args.get("date")

        # Handle both old time format and new hour/minute format
        selected_time = request.args.get("time")
        selected_hour = request.args.get("hour")
        selected_minute = request.args.get("minute")

        if not doctor_id or not selected_date:
            flash("Invalid appointment details.", "danger")
            return redirect("/dashboard")

        # Convert hour/minute to time format if needed
        if selected_hour and selected_minute:
            selected_time = f"{int(selected_hour):02d}:{int(selected_minute):02d}"
        elif not selected_time:
            flash("Invalid appointment details.", "danger")
            return redirect("/dashboard")

        selected_slots = [{"date": selected_date, "time": selected_time}]
        payment_method = 'wallet'

    conn = get_db()
    doctor = conn.execute("SELECT * FROM doctors WHERE id=?", (doctor_id,)).fetchone()
    wallet = conn.execute("SELECT balance FROM wallets WHERE user_id=?", (session["user_id"],)).fetchone()
    conn.close()

    if not doctor:
        flash("Doctor not found.", "danger")
        return redirect("/dashboard")

    # Always use category/specialty based fees for user-side payment flow
    doctor = dict(doctor)
    doctor_fees = get_specialty_fees(doctor["specialty"])
    doctor["fees"] = doctor_fees

    wallet_balance = wallet["balance"] if wallet else 0
    total_fees = doctor_fees * len(selected_slots)

    if wallet_balance < total_fees:
        # Store payment data in session for returning from wallet flow
        session['payment_data'] = {
            'doctor_id': doctor_id,
            'slot_count': len(selected_slots),
            'slots': selected_slots,
            'total_fees': total_fees,
            'payment_method': 'wallet'
        }
        session['wallet_back_url'] = '/payment'
        flash(f"Insufficient wallet balance! Please add ₹{total_fees - wallet_balance:.2f} to your wallet.", "warning")
        # Don't redirect - let the template handle insufficient balance

    # Set back URL to booking page so payment can link back properly
    back_url = f"/book_slots/{doctor_id}"
    return render_template("payment.html", doctor=doctor, selected_slots=selected_slots, wallet_balance=wallet_balance, total_fees=total_fees, back_url=back_url)

# ---------------- CONFIRM PAYMENT ----------------
@app.route("/confirm_payment", methods=["POST"])
def confirm_payment():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    doctor_id = request.form["doctor_id"]
    slot_count = int(request.form.get("slot_count", 1))
    payment_method = request.form["payment_method"]

    conn = get_db()

    # Get doctor specialty to calculate category-based fees
    doctor = conn.execute("SELECT specialty FROM doctors WHERE id=?", (doctor_id,)).fetchone()
    if not doctor:
        conn.close()
        flash("Doctor not found.", "danger")
        return redirect("/dashboard")

    fees_per_slot = get_specialty_fees(doctor["specialty"])
    total_fees = fees_per_slot * slot_count

    # Collect all slots
    slots = []
    for i in range(slot_count):
        date = request.form.get(f"date_{i}")
        time = request.form.get(f"time_{i}")
        if date and time:
            slots.append({"date": date, "time": time})

    if not slots:
        conn.close()
        flash("No slots selected.", "danger")
        return redirect(f"/book_slots/{doctor_id}")

    # Remove duplicates
    unique_slots = []
    seen = set()
    for slot in slots:
        key = (slot['date'], slot['time'])
        if key not in seen:
            seen.add(key)
            unique_slots.append(slot)
    slots = unique_slots

    # --- Server-side validation: prevent double-booking ---
    now = datetime.now()

    for slot in slots:
        try:
            appt_dt = datetime.strptime(f"{slot['date']} {slot['time']}", "%Y-%m-%d %H:%M")
        except Exception:
            conn.close()
            flash("Invalid date/time format.", "danger")
            return redirect(f"/book_slots/{doctor_id}")

        # Prevent booking slots in the past
        if appt_dt <= now:
            conn.close()
            flash("Cannot book slots in the past.", "danger")
            return redirect(f"/book_slots/{doctor_id}")

        # Check if slot already taken (accepted or pending) - prevent double booking
        existing = conn.execute("""
            SELECT COUNT(*) as cnt FROM appointments
            WHERE doctor_id=? AND date=? AND time=? AND status IN ('accepted','pending')
        """, (doctor_id, slot['date'], slot['time'])).fetchone()
        if existing and existing["cnt"] > 0:
            conn.close()
            flash("This slot is already booked. Please choose different slots.", "danger")
            return redirect(f"/book_slots/{doctor_id}")

    # Check wallet balance if paying with wallet
    if payment_method == "wallet":
        wallet = conn.execute("SELECT balance FROM wallets WHERE user_id=?", (session["user_id"],)).fetchone()
        if not wallet:
            # Create wallet if it doesn't exist
            conn.execute("INSERT INTO wallets (user_id, balance) VALUES (?, 0.0)", (session["user_id"],))
            conn.commit()
            wallet = conn.execute("SELECT balance FROM wallets WHERE user_id=?", (session["user_id"],)).fetchone()
        if wallet["balance"] < total_fees:
            # Store payment data in session for when user returns from wallet
            session['payment_data'] = {
                'doctor_id': doctor_id,
                'slot_count': slot_count,
                'slots': slots,
                'payment_method': payment_method
            }
            conn.close()
            session['return_url'] = '/payment'
            session['wallet_back_url'] = '/payment'
            flash("Insufficient wallet balance. Please add money to your wallet or choose another payment method.", "danger")
            return redirect("/wallet")

        # Deduct from wallet
        conn.execute("UPDATE wallets SET balance = balance - ? WHERE user_id=?", (total_fees, session["user_id"]))

    # Insert appointments and payments
    appointment_ids = []
    for slot in slots:
        # Insert appointment
        conn.execute("""
            INSERT INTO appointments (patient_id, doctor_id, date, time, address, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, (session["user_id"], doctor_id, slot['date'], slot['time'], "Patient's address"))

        # Get appointment ID
        appointment_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        appointment_ids.append(appointment_id)

        # Process payment
        payment_details = ""
        if payment_method == "card":
            card_number = request.form.get("card_number", "")
            expiry_date = request.form.get("expiry_date", "")
            cvv = request.form.get("cvv", "")
            payment_details = f"Card: **** **** **** {card_number[-4:] if card_number else ''}"
        elif payment_method == "upi":
            upi_id = request.form.get("upi_id", "")
            payment_details = f"UPI: {upi_id}"

        # Record payment
        conn.execute("""
            INSERT INTO payments (appointment_id, user_id, amount, payment_method, payment_details, status)
            VALUES (?, ?, ?, ?, ?, 'completed')
        """, (appointment_id, session["user_id"], fees_per_slot, payment_method, payment_details))

    # Get doctor user_id
    doctor_user_id = conn.execute("SELECT user_id FROM doctors WHERE id=?", (doctor_id,)).fetchone()["user_id"]

    # Create notification for doctor
    slot_details = ", ".join([f"{slot['date']} {slot['time']}" for slot in slots])
    message = f"New appointment requests from patient ({slot_count} slots). Dates/Times: {slot_details}. Payment: {payment_method}"
    conn.execute("""
        INSERT INTO notifications (user_id, message, type, appointment_id)
        VALUES (?, ?, 'appointment_request', ?)
    """, (doctor_user_id, message, appointment_ids[0]))  # Use first appointment ID for notification

    conn.commit()
    conn.close()

    flash(f"{slot_count} appointment request(s) sent successfully! ₹{total_fees} has been deducted from your wallet.", "success")
    return redirect("/appointments")

# ---------------- WALLET ----------------
@app.route("/wallet")
def wallet():
    if "user_id" not in session or session.get("role") not in ["user", "doctor"]:
        return redirect("/login")

    # Get back URL from request parameter or session
    back_url = request.args.get('back') or session.get('wallet_back_url')
    if back_url:
        session['wallet_back_url'] = back_url
    
    conn = get_db()

    # Get wallet data
    wallet_data = conn.execute("SELECT * FROM wallets WHERE user_id=?", (session["user_id"],)).fetchone()

    if not wallet_data:
        # Create wallet if it doesn't exist
        conn.execute("INSERT INTO wallets (user_id, balance) VALUES (?, 0.0)", (session["user_id"],))
        conn.commit()
        wallet_data = conn.execute("SELECT * FROM wallets WHERE user_id=?", (session["user_id"],)).fetchone()

    # Get transaction history based on user role
    if session.get("role") == "doctor":
        # For doctors: show credits from accepted appointments
        transactions = conn.execute("""
            SELECT
                'credit' as type,
                DATE(p.created_at) as date,
                p.amount
            FROM payments p
            JOIN appointments a ON p.appointment_id = a.id
            JOIN doctors d ON a.doctor_id = d.id
            WHERE d.user_id=? AND p.status='completed' AND a.status='accepted'
            ORDER BY p.created_at DESC
            LIMIT 20
        """, (session["user_id"],)).fetchall()
    else:
        # For patients: show debits for payments and credits for refunds
        transactions = conn.execute("""
            SELECT
                CASE
                    WHEN appointment_id IS NOT NULL AND status != 'refunded' THEN 'debit'
                    WHEN appointment_id IS NOT NULL AND status = 'refunded' THEN 'credit'
                    ELSE 'credit'
                END as type,
                DATE(created_at) as date,
                ABS(amount) as amount
            FROM payments
            WHERE user_id=?
            ORDER BY created_at DESC
            LIMIT 20
        """, (session["user_id"],)).fetchall()

    conn.close()

    balance = wallet_data["balance"] if wallet_data else 0.0

    # Determine back URL with fallback
    if not back_url:
        back_url = '/doctor/dashboard' if session.get('role') == 'doctor' else '/dashboard'

    return render_template("wallet.html", balance=balance, transactions=transactions, back_url=back_url)

# ---------------- ADD MONEY TO WALLET ----------------
@app.route("/add_money", methods=["POST"])
def add_money():
    if "user_id" not in session:
        flash("Please login to access your wallet.", "danger")
        return redirect("/login")

    # Validate amount
    try:
        amount = float(request.form.get("amount", "").strip())
    except (ValueError, TypeError):
        flash("Please enter a valid amount.", "danger")
        return redirect("/wallet")

    # Validate amount range
    if amount <= 0:
        flash("Amount must be greater than zero.", "danger")
        return redirect("/wallet")

    if amount > 50000:  # Maximum limit for single transaction
        flash("Maximum amount per transaction is ₹50,000.", "danger")
        return redirect("/wallet")

    # Validate payment method
    payment_method = request.form.get("payment_method", "").strip()
    valid_payment_methods = ["Wallet", "UPI", "Card"]

    if payment_method not in valid_payment_methods:
        flash("Please select a valid payment method.", "danger")
        return redirect("/wallet")

    # Check if amount has more than 2 decimal places
    if round(amount, 2) != amount:
        flash("Amount can have at most 2 decimal places.", "danger")
        return redirect("/wallet")

    conn = get_db()

    try:
        # Update wallet balance
        conn.execute("""
            UPDATE wallets SET balance = balance + ? WHERE user_id=?
        """, (amount, session["user_id"]))

        # Record payment
        conn.execute("""
            INSERT INTO payments (user_id, amount, payment_method, status)
            VALUES (?, ?, ?, 'completed')
        """, (session["user_id"], amount, payment_method))

        conn.commit()

        flash(f"₹{amount:.2f} added to your wallet successfully!", "success")

        # Check if there's a booking flow to return to
        if 'payment_data' in session:
            # Redirect back to payment page to complete the booking
            return redirect('/payment?auto_proceed=1')
        
        # Check if there's a return URL in session (for other flows)
        return_url = session.pop('return_url', None)
        if return_url:
            return redirect(return_url)

    except Exception as e:
        conn.rollback()
        flash("An error occurred while processing your request. Please try again.", "danger")
        print(f"Error adding money to wallet: {e}")

    finally:
        conn.close()

    return redirect("/wallet")

# ---------------- PROCESS PAYMENT ----------------
@app.route("/process_payment", methods=["POST"])
def process_payment():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    payment_method = request.form["payment_method"]
    amount = float(request.form["amount"])

    # For now, just redirect back to wallet with success message
    # In a real app, this would integrate with payment gateways
    flash(f"Payment of ₹{amount:.2f} processed successfully via {payment_method}!", "success")
    return redirect("/wallet")

# ---------------- ACCEPT APPOINTMENT ----------------
@app.route("/accept_appointment/<int:appointment_id>", methods=["POST"])
def accept_appointment(appointment_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return redirect("/login")

    conn = get_db()

    # Get appointment details first to verify it exists and belongs to the doctor
    appointment = conn.execute("""
        SELECT a.*, d.fees, d.specialty FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.id=? AND d.user_id=?
    """, (appointment_id, session["user_id"])).fetchone()

    if not appointment:
        conn.close()
        flash("Appointment not found or access denied.", "danger")
        return redirect("/doctor/notifications")

    # Calculate fees based on doctor's stored fees or specialty
    fees = appointment['fees'] if appointment['fees'] and appointment['fees'] > 0 else get_specialty_fees(appointment['specialty'])

    # Update appointment status to accepted
    conn.execute("""
        UPDATE appointments SET status='accepted' WHERE id=?
    """, (appointment_id,))

    # Add fees to doctor's wallet
    conn.execute("""
        UPDATE wallets SET balance = balance + ? WHERE user_id=?
    """, (fees, session["user_id"]))

    # Create notification for patient
    patient_message = f"Your appointment with Dr. {session.get('name')} on {appointment['date']} at {appointment['time']} has been accepted. ₹{fees} has been deducted from your wallet."
    conn.execute("""
        INSERT INTO notifications (user_id, message, type)
        VALUES (?, ?, 'appointment_accepted')
    """, (appointment['patient_id'], patient_message))

    # Delete the notification from doctor's view
    conn.execute("""
        DELETE FROM notifications WHERE appointment_id=? AND type='appointment_request'
    """, (appointment_id,))

    conn.commit()
    conn.close()

    flash("Appointment accepted successfully!", "success")
    return redirect("/doctor/dashboard")

# ---------------- REJECT APPOINTMENT ----------------
@app.route("/reject_appointment/<int:appointment_id>", methods=["POST"])
def reject_appointment(appointment_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return redirect("/login")

    delete_notification = request.args.get('delete_notification', 'false').lower() == 'true'

    conn = get_db()

    # Get appointment details first to verify it exists and belongs to the doctor
    appointment = conn.execute("""
        SELECT a.*, d.fees
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.id=? AND d.user_id=?
    """, (appointment_id, session["user_id"])).fetchone()

    if not appointment:
        conn.close()
        flash("Appointment not found or access denied.", "danger")
        return redirect("/doctor/notifications")

    # Guard invalid transitions
    if appointment['status'] == 'rejected':
        conn.close()
        flash("Appointment already rejected.", "info")
        return redirect("/doctor/notifications")

    if appointment['status'] not in ('pending', 'accepted'):
        conn.close()
        flash("Only pending/accepted appointments can be rejected.", "warning")
        return redirect("/doctor/notifications")

    # Refund based on actual completed payment to avoid wrong/double refunds
    payment = conn.execute("""
        SELECT id, amount
        FROM payments
        WHERE appointment_id=? AND status='completed'
        ORDER BY id DESC
        LIMIT 1
    """, (appointment_id,)).fetchone()
    refund_amount = payment['amount'] if payment else 0

    # Reject appointment
    conn.execute("""
        UPDATE appointments SET status='rejected' WHERE id=?
    """, (appointment_id,))

    if refund_amount > 0:
        # Refund patient wallet
        conn.execute("""
            UPDATE wallets SET balance = balance + ? WHERE user_id=?
        """, (refund_amount, appointment['patient_id']))

        # If appointment was accepted before reject, doctor already got paid
        if appointment['status'] == 'accepted':
            conn.execute("""
                UPDATE wallets SET balance = balance - ? WHERE user_id=?
            """, (refund_amount, session["user_id"]))

        # Mark payment refunded so this path is idempotent
        conn.execute("""
            UPDATE payments SET status='refunded' WHERE id=?
        """, (payment['id'],))

    # Create notification for patient
    if refund_amount > 0:
        patient_message = f"Your appointment with Dr. {session.get('name')} on {appointment['date']} at {appointment['time']} has been rejected. Rs {refund_amount} has been refunded to your wallet."
    else:
        patient_message = f"Your appointment with Dr. {session.get('name')} on {appointment['date']} at {appointment['time']} has been rejected."

    conn.execute("""
        INSERT INTO notifications (user_id, message, type)
        VALUES (?, ?, 'appointment_rejected')
    """, (appointment['patient_id'], patient_message))

    # Mark notification as read and optionally delete it
    if delete_notification:
        conn.execute("""
            DELETE FROM notifications WHERE appointment_id=? AND type='appointment_request'
        """, (appointment_id,))
    else:
        conn.execute("""
            UPDATE notifications SET read=1 WHERE appointment_id=? AND type='appointment_request'
        """, (appointment_id,))

    conn.commit()
    conn.close()

    flash("Appointment rejected successfully." + (f" Rs {refund_amount} refunded." if refund_amount > 0 else ""), "info")
    return redirect("/doctor/notifications")

# ---------------- CANCEL APPOINTMENT (DOCTOR) ----------------
@app.route("/doctor/cancel_appointment/<int:appointment_id>", methods=["POST"])
def doctor_cancel_appointment(appointment_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return {"success": False, "message": "Unauthorized"}, 401

    conn = get_db()

    # Get appointment details
    appointment = conn.execute("""
        SELECT a.*, d.fees FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.id=? AND a.doctor_id=(SELECT id FROM doctors WHERE user_id=?)
    """, (appointment_id, session["user_id"])).fetchone()

    if not appointment:
        conn.close()
        return {"success": False, "message": "Appointment not found"}, 404

    # Calculate fees based on doctor's stored fees or specialty
    fees = appointment['fees'] if appointment['fees'] and appointment['fees'] > 0 else get_specialty_fees(appointment['specialty'])

    # Refund fee to patient's wallet
    conn.execute("""
        UPDATE wallets SET balance = balance + ? WHERE user_id=?
    """, (fees, appointment['patient_id']))

    # If appointment was accepted, deduct the fee from doctor's wallet
    # (since doctor already received the payment when accepting)
    if appointment['status'] == 'accepted':
        conn.execute("""
            UPDATE wallets SET balance = balance - ? WHERE user_id=?
        """, (fees, session["user_id"]))

    # Update payment status to refunded
    conn.execute("""
        UPDATE payments SET status='refunded' WHERE appointment_id=?
    """, (appointment_id,))

    # Delete the appointment
    conn.execute("DELETE FROM appointments WHERE id=?", (appointment_id,))

    # Create notification for patient
    patient_message = f"Your appointment with Dr. {session.get('name')} on {appointment['date']} at {appointment['time']} has been cancelled. ₹{fees} has been refunded to your wallet."
    conn.execute("""
        INSERT INTO notifications (user_id, message, type)
        VALUES (?, ?, 'appointment_cancelled')
    """, (appointment['patient_id'], patient_message))

    conn.commit()
    conn.close()

    return {"success": True, "message": "Appointment cancelled successfully"}

# ---------------- APPOINTMENT DETAILS ----------------
@app.route("/appointment_details/<int:appointment_id>")
def appointment_details(appointment_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return {"success": False, "message": "Unauthorized"}, 401

    conn = get_db()
    appointment = conn.execute("""
        SELECT a.*, u.fname as patient_fname, u.lname as patient_lname, u.patient_city, u.patient_state, u.patient_pincode, u.patient_address
        FROM appointments a
        JOIN users u ON a.patient_id = u.id
        WHERE a.id=? AND a.doctor_id=(SELECT id FROM doctors WHERE user_id=?)
    """, (appointment_id, session["user_id"])).fetchone()
    conn.close()

    if not appointment:
        return {"success": False, "message": "Appointment not found"}, 404

    return {"success": True, "appointment": dict(appointment)}

# ---------------- DOCTOR PATIENTS LIST ----------------
@app.route("/doctor/patients")
def doctor_patients():
    if "user_id" not in session or session.get("role") != "doctor":
        return {"success": False, "message": "Unauthorized"}, 401

    conn = get_db()

    # Get doctor's ID and hospital address
    doctor = conn.execute("SELECT id, full_address, city, state, pincode, latitude, longitude FROM doctors WHERE user_id=?", (session["user_id"],)).fetchone()
    doctor_id = doctor["id"]

    # Get all patients who have had appointments with this doctor
    patients = conn.execute("""
        SELECT
            u.id,
            u.fname,
            u.lname,
            u.email,
            u.patient_address,
            u.patient_city,
            u.patient_state,
            u.patient_pincode,
            u.patient_latitude,
            u.patient_longitude,
            COUNT(a.id) as appointment_count,
            MAX(a.date) as last_visit
        FROM users u
        LEFT JOIN appointments a ON u.id = a.patient_id AND a.doctor_id=?
        WHERE u.role='user' AND (a.id IS NOT NULL OR u.id IN (
            SELECT DISTINCT patient_id FROM appointments WHERE doctor_id=?
        ))
        GROUP BY u.id, u.fname, u.lname, u.email, u.patient_address, u.patient_city, u.patient_state, u.patient_pincode, u.patient_latitude, u.patient_longitude
        ORDER BY last_visit DESC, appointment_count DESC
    """, (doctor_id, doctor_id)).fetchall()

    # Calculate distances for each patient
    patients_with_distance = []
    for patient in patients:
        patient_dict = dict(patient)

        # Calculate distance from patient to doctor's hospital
        doctor_addr = {
            'city': doctor['city'],
            'state': doctor['state'],
            'pincode': doctor['pincode'],
            'address': doctor['full_address'],
            'latitude': doctor['latitude'],
            'longitude': doctor['longitude']
        }

        patient_addr = {
            'city': patient['patient_city'],
            'state': patient['patient_state'],
            'pincode': patient['patient_pincode'],
            'address': patient['patient_address'],
            'latitude': patient['patient_latitude'],
            'longitude': patient['patient_longitude']
        }

        distance = DistanceCalculator.calculate_distance(patient_addr, doctor_addr)
        patient_dict['distance'] = f"{distance:.1f} km" if distance is not None else "N/A"

        patients_with_distance.append(patient_dict)

    conn.close()

    return {"success": True, "patients": patients_with_distance}

# ---------------- DELETE APPOINTMENTS BY DATE ----------------
@app.route("/delete_appointments_by_date", methods=["POST"])
def delete_appointments_by_date():
    if "user_id" not in session or session.get("role") != "doctor":
        return {"success": False, "message": "Unauthorized"}, 401
 
    selected_date = request.form.get("date")
    if not selected_date:
        return {"success": False, "message": "Date not provided"}, 400

    conn = get_db()

    # Get doctor's ID
    doctor_id = conn.execute("SELECT id FROM doctors WHERE user_id=?", (session["user_id"],)).fetchone()["id"]

    # Get all appointments for this date and doctor
    appointments = conn.execute("""
        SELECT a.*, d.fees FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.doctor_id=? AND a.date=? AND a.status='accepted'
    """, (doctor_id, selected_date)).fetchall()

    if not appointments:
        conn.close()
        return {"success": False, "message": "No appointments found for this date"}, 404

    # Process each appointment
    for appointment in appointments:
        # Refund fee to patient's wallet
        conn.execute("""
            UPDATE wallets SET balance = balance + ? WHERE user_id=?
        """, (appointment['fees'], appointment['patient_id']))

        # Update payment status to refunded
        conn.execute("""
            UPDATE payments SET status='refunded' WHERE appointment_id=?
        """, (appointment['id'],))

        # Create notification for patient
        patient_message = f"Your appointment with Dr. {session.get('name')} on {appointment['date']} at {appointment['time']} has been cancelled. ₹{appointment['fees']} has been refunded to your wallet."
        conn.execute("""
            INSERT INTO notifications (user_id, message, type)
            VALUES (?, ?, 'appointment_cancelled')
        """, (appointment['patient_id'], patient_message))

        # Delete the appointment
        conn.execute("DELETE FROM appointments WHERE id=?", (appointment['id'],))

    conn.commit()
    conn.close()

    return {"success": True, "message": f"All appointments for {selected_date} have been cancelled and patients refunded"}

# ---------------- CHAT ----------------
@app.route("/chat/<int:doctor_id>")
def chat(doctor_id):
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()
    doctor = conn.execute("SELECT * FROM doctors WHERE id=?", (doctor_id,)).fetchone()
    conn.close()

    if not doctor:
        return "Doctor not found", 404

    return render_template("chat.html", doctor=doctor)

# ---------------- CONSULT APPOINTMENT ----------------
@app.route("/consult/<int:appointment_id>")
def consult_appointment(appointment_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return redirect("/login")

    conn = get_db()

    # Get appointment details with patient and doctor info
    appointment = conn.execute("""
        SELECT a.*, u.fname as patient_fname, u.lname as patient_lname,
               u.email as patient_email, u.patient_city, u.patient_state,
               u.patient_address, d.fname as doctor_fname, d.lname as doctor_lname
        FROM appointments a
        JOIN users u ON a.patient_id = u.id
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.id=? AND a.doctor_id=(SELECT id FROM doctors WHERE user_id=?)
    """, (appointment_id, session["user_id"])).fetchone()

    # Get unread notifications count
    notifications_count = conn.execute("SELECT COUNT(*) as count FROM notifications WHERE user_id=? AND read=0", (session["user_id"],)).fetchone()["count"]

    conn.close()

    if not appointment:
        flash("Appointment not found or access denied.", "danger")
        return redirect("/doctor/appointments")

    return render_template("consult_appointment.html", appointment=dict(appointment), notifications_count=notifications_count)

# ---------------- PRESCRIPTION PAGE ----------------
@app.route("/prescription/<int:appointment_id>")
def prescription_page(appointment_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return redirect("/login")

    conn = get_db()

    # Get appointment details with patient and doctor info
    appointment = conn.execute("""
        SELECT a.*, u.fname as patient_fname, u.lname as patient_lname,
               u.email as patient_email, u.patient_city, u.patient_state,
               u.patient_address, d.fname as doctor_fname, d.lname as doctor_lname
        FROM appointments a
        JOIN users u ON a.patient_id = u.id
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.id=? AND a.doctor_id=(SELECT id FROM doctors WHERE user_id=?)
    """, (appointment_id, session["user_id"])).fetchone()

    # Get existing prescriptions for this appointment
    prescriptions = conn.execute("""
        SELECT * FROM prescriptions
        WHERE appointment_id=?
        ORDER BY created_at ASC
    """, (appointment_id,)).fetchall()

    conn.close()

    if not appointment:
        flash("Appointment not found or access denied.", "danger")
        return redirect("/doctor/appointments")

    return render_template("prescription.html", appointment=dict(appointment), prescriptions=[dict(p) for p in prescriptions])

# ---------------- SAVE PRESCRIPTION ----------------
@app.route("/save_prescription/<int:appointment_id>", methods=["POST"])
def save_prescription(appointment_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return {"success": False, "message": "Unauthorized"}, 401

    try:
        data = request.get_json()
        medicines = data.get("medicines", [])

        if not medicines:
            return {"success": False, "message": "No medicines provided"}, 400

        conn = get_db()

        # Verify appointment belongs to doctor
        appointment = conn.execute("""
            SELECT id FROM appointments
            WHERE id=? AND doctor_id=(SELECT id FROM doctors WHERE user_id=?)
        """, (appointment_id, session["user_id"])).fetchone()

        if not appointment:
            conn.close()
            return {"success": False, "message": "Appointment not found or access denied"}, 404

        # Insert each medicine
        for medicine in medicines:
            tablets = medicine["tablets"] or 1
            duration = medicine["duration"] or 1
            medicine_supplied = tablets * duration
            
            conn.execute("""
                INSERT INTO prescriptions
                (appointment_id, medicine_name, tablets, timing, before_after_eat, price, duration, medicine_supplied, medicine_consumed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                appointment_id,
                medicine["medicine_name"],
                tablets,
                medicine["timing"],
                medicine["before_after_eat"],
                medicine["price"],
                duration,
                medicine_supplied,
                0
            ))

        conn.commit()
        conn.close()

        return {"success": True, "message": "Prescription saved successfully"}

    except Exception as e:
        print(f"Error saving prescription: {e}")
        return {"success": False, "message": "Error saving prescription"}, 500

# ---------------- UPDATE MEDICINE CONSUMED ----------------
@app.route("/update_medicine_consumed/<int:prescription_id>", methods=["POST"])
def update_medicine_consumed(prescription_id):
    if "user_id" not in session or session.get("role") != "user":
        return {"success": False, "message": "Unauthorized"}, 401

    try:
        data = request.get_json()
        medicine_consumed = data.get("medicine_consumed", 0)

        conn = get_db()

        # Verify prescription belongs to patient
        prescription = conn.execute("""
            SELECT p.id, a.patient_id, p.medicine_supplied
            FROM prescriptions p
            JOIN appointments a ON p.appointment_id = a.id
            WHERE p.id=? AND a.patient_id=?
        """, (prescription_id, session["user_id"])).fetchone()

        if not prescription:
            conn.close()
            return {"success": False, "message": "Prescription not found or access denied"}, 404

        # Validate consumed amount doesn't exceed supplied
        if medicine_consumed > prescription["medicine_supplied"] or medicine_consumed < 0:
            conn.close()
            return {"success": False, "message": "Invalid consumed amount"}, 400

        # Update medicine consumed
        conn.execute("""
            UPDATE prescriptions
            SET medicine_consumed = ?
            WHERE id = ?
        """, (medicine_consumed, prescription_id))

        conn.commit()
        conn.close()

        return {"success": True, "message": "Medicine consumed updated successfully"}

    except Exception as e:
        print(f"Error updating medicine consumed: {e}")
        return {"success": False, "message": "Error updating medicine consumed"}, 500

# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully", "success")
    return redirect("/login")

# ---------------- DOCTORS REPORT ----------------
@app.route("/doctors_report")
def doctors_report():
    conn = get_db()
    doctors = conn.execute("""
        SELECT specialty, fname, lname, fees
        FROM doctors
        WHERE profile_complete=1
        ORDER BY specialty, fees
    """).fetchall()
    conn.close()
    return render_template("doctors_report.html", doctors=doctors)

# ---------------- ADD CUSTOMER ----------------
@app.route("/api/customers", methods=["POST"])
def add_customer():
    if "user_id" not in session:
        return {"success": False, "message": "Unauthorized"}, 401

    data = request.get_json()
    conn = None
    try:
        conn = get_db()
        email = (data.get('email') or '').strip().lower()
        mobile = (data.get('mobile') or '').strip()

        existing_email = conn.execute(
            "SELECT id FROM users WHERE lower(email)=?",
            (email,)
        ).fetchone()
        if existing_email:
            return {"success": False, "message": "Email already registered."}

        existing_mobile = conn.execute(
            "SELECT id FROM users WHERE mobile=?",
            (mobile,)
        ).fetchone()
        if existing_mobile:
            return {"success": False, "message": "Mobile number already registered."}

        conn.execute("""INSERT INTO users (fname, lname, email, mobile, patient_city, patient_state, role) VALUES (?, ?, ?, ?, ?, ?, 'user')""", (data['fname'], data['lname'], email, mobile, data['city'], data['state']))
        conn.commit()
        return {"success": True}
    except sqlite3.IntegrityError as e:
        if conn:
            conn.rollback()
        if "users.email" in str(e):
            return {"success": False, "message": "Email already registered."}
        elif "users.mobile" in str(e):
            return {"success": False, "message": "Mobile number already registered."}
        else:
            return {"success": False, "message": "Registration failed."}
    finally:
        if conn:
            conn.close()

@app.route("/api/customers", methods=["GET"])
def get_customers():
    if "user_id" not in session:
        return {"success": False, "message": "Unauthorized"}, 401

    conn = get_db()
    customers = conn.execute("SELECT * FROM users WHERE role='user' ORDER BY fname, lname").fetchall()
    conn.close()
    return {"success": True, "customers": [dict(c) for c in customers]}

# ---------------- CUSTOMERS LIST ----------------
@app.route("/customers")
def customers():
    if "user_id" not in session:
        return redirect("/login")

    return render_template("customers.html")

# ---------------- MERCHANTS PAGE ----------------
@app.route("/merchants")
def merchants():
    if "user_id" not in session:
        return redirect("/login")

    return render_template("merchants.html")

# ---------------- WEIGHT TRACKING ----------------
@app.route("/add_weight", methods=["GET", "POST"])
def add_weight():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    if request.method == "POST":
        weight = float(request.form["weight"])
        date = request.form.get("date")
        notes = request.form.get("notes", "")

        if not date:
            from datetime import datetime
            date = datetime.now().strftime('%Y-%m-%d')

        conn = get_db()
        conn.execute("""
            INSERT INTO weight_tracking (user_id, weight, date, notes)
            VALUES (?, ?, ?, ?)
        """, (session["user_id"], weight, date, notes))
        conn.commit()
        conn.close()

        flash("Weight recorded successfully!", "success")
        return redirect("/weight_history")

    return render_template("add_weight.html")

@app.route("/weight_history")
def weight_history():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()
    weights = conn.execute("""
        SELECT * FROM weight_tracking
        WHERE user_id=?
        ORDER BY date DESC, created_at DESC
    """, (session["user_id"],)).fetchall()
    conn.close()

    return render_template("weight_history.html", weights=weights)

def _style_vitals_axes(ax, fig):
    """Shared premium chart styling for patient vitals."""
    fig.patch.set_facecolor('#0b0f1a')
    ax.set_facecolor('#121b2d')

    ax.spines['left'].set_color('#334155')
    ax.spines['bottom'].set_color('#334155')
    ax.spines['top'].set_color('#121b2d')
    ax.spines['right'].set_color('#121b2d')

    ax.tick_params(colors='#cbd5e1', labelsize=9)
    ax.grid(axis='y', color='#334155', alpha=0.22, linestyle='-', linewidth=0.8)
    ax.grid(axis='x', alpha=0.0)

def _plot_glow_series(ax, x, y, color, label=None, show_slope=False, single_point_baseline=None):
    """Draw a matte pro line series with optional slope trendline."""
    if len(x) == 1 and len(y) == 1 and single_point_baseline is not None:
        # Keep connector visible even with a single sample.
        x0 = x[0]
        ax.plot(
            [x0 - 0.35, x0],
            [single_point_baseline, y[0]],
            color=color,
            linewidth=2.4,
            alpha=0.95,
            solid_joinstyle='round',
            solid_capstyle='round',
            label=label
        )
    else:
        ax.plot(
            x, y, color=color, linewidth=2.4, alpha=0.95,
            solid_joinstyle='round', solid_capstyle='round', label=label
        )
    ax.scatter(x, y, s=14, color=color, alpha=0.9, zorder=4)

    if y:
        ax.scatter([x[-1]], [y[-1]], s=34, color=color, edgecolors='#0b0f1a', linewidths=0.9, zorder=5)
        ax.annotate(
            f"{y[-1]:.1f}" if isinstance(y[-1], float) else f"{y[-1]}",
            (x[-1], y[-1]),
            textcoords="offset points",
            xytext=(8, -10),
            color='#e2e8f0',
            fontsize=8.5,
            fontweight='bold'
        )

    # Add linear slope/trend line for better direction visibility
    if show_slope and len(y) >= 2:
        n = len(x)
        sx = sum(x)
        sy = sum(y)
        sxy = sum(x[i] * y[i] for i in range(n))
        sx2 = sum(xi * xi for xi in x)
        den = (n * sx2) - (sx * sx)
        if den != 0:
            slope = ((n * sxy) - (sx * sy)) / den
            intercept = (sy - slope * sx) / n
            trend = [slope * xi + intercept for xi in x]
            ax.plot(x, trend, color='#94a3b8', linewidth=1.0, linestyle='--', alpha=0.45)

@app.route("/weight_chart")
def weight_chart():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    try:
        import matplotlib
        matplotlib.use('Agg')  # Use non-interactive backend
        import matplotlib.pyplot as plt
        from io import BytesIO
    except ImportError:
        return {"success": False, "message": "Matplotlib not available"}, 500

    conn = get_db()
    weights = conn.execute("""
        SELECT date, weight FROM weight_tracking
        WHERE user_id=? AND weight IS NOT NULL
        ORDER BY date ASC, id ASC
    """, (session["user_id"],)).fetchall()
    conn.close()

    fig, ax = plt.subplots(figsize=(12, 6.6), dpi=120)
    _style_vitals_axes(ax, fig)

    if weights:
        dates = [row['date'] for row in weights]
        x_pos = list(range(len(dates)))
        weight_values = [row['weight'] for row in weights]
        _plot_glow_series(ax, x_pos, weight_values, '#22d3ee', show_slope=True, single_point_baseline=0)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(dates)
    else:
        ax.text(
            0.5, 0.5, 'No weight data available',
            transform=ax.transAxes,
            ha='center', va='center',
            color='#94a3b8', fontsize=12, fontweight='semibold'
        )
        ax.set_xticks([])
        ax.set_yticks([])

    ax.set_title('Weight History', color='#e2e8f0', fontsize=13, pad=12, fontweight='bold')
    ax.set_xlabel('Date', color='#cbd5e1')
    ax.set_ylabel('Weight (kg)', color='#cbd5e1')
    plt.xticks(rotation=28, ha='right')
    plt.tight_layout()

    buffer = BytesIO()
    plt.savefig(
        buffer,
        format='png',
        dpi=120,
        bbox_inches='tight',
        pad_inches=0.08,
        facecolor=fig.get_facecolor(),
        edgecolor=fig.get_facecolor(),
        transparent=False
    )
    buffer.seek(0)
    plt.close(fig)

    from flask import make_response
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'image/png'
    return response

@app.route("/height_chart")
def height_chart():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from io import BytesIO
    except ImportError:
        return {"success": False, "message": "Matplotlib not available"}, 500

    conn = get_db()
    heights = conn.execute("""
        SELECT date, height FROM weight_tracking
        WHERE user_id=? AND height IS NOT NULL
        ORDER BY date ASC, id ASC
    """, (session["user_id"],)).fetchall()
    conn.close()

    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=120)
    _style_vitals_axes(ax, fig)

    if heights:
        dates = [row['date'] for row in heights]
        x_pos = list(range(len(dates)))
        height_values = [row['height'] for row in heights]
        _plot_glow_series(ax, x_pos, height_values, '#2dd4bf', show_slope=True, single_point_baseline=0)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(dates)
    else:
        ax.text(
            0.5, 0.5, 'No height data available',
            transform=ax.transAxes,
            ha='center', va='center',
            color='#94a3b8', fontsize=12, fontweight='semibold'
        )
        ax.set_xticks([])
        ax.set_yticks([])

    ax.set_title('Height History', color='#e2e8f0', fontsize=13, pad=12, fontweight='bold')
    ax.set_xlabel('Date', color='#cbd5e1')
    ax.set_ylabel('Height (cm)', color='#cbd5e1')
    plt.xticks(rotation=28, ha='right')
    plt.tight_layout()

    buffer = BytesIO()
    plt.savefig(
        buffer,
        format='png',
        dpi=120,
        bbox_inches='tight',
        pad_inches=0.08,
        facecolor=fig.get_facecolor(),
        edgecolor=fig.get_facecolor(),
        transparent=False
    )
    buffer.seek(0)
    plt.close(fig)

    from flask import make_response
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'image/png'
    return response

@app.route("/bp_chart")
def bp_chart():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from io import BytesIO
    except ImportError:
        return {"success": False, "message": "Matplotlib not available"}, 500

    conn = get_db()
    bp_rows = conn.execute("""
        SELECT date, bp_systolic, bp_diastolic FROM weight_tracking
        WHERE user_id=? AND bp_systolic IS NOT NULL AND bp_diastolic IS NOT NULL
        ORDER BY date ASC, id ASC
    """, (session["user_id"],)).fetchall()
    conn.close()

    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=120)
    _style_vitals_axes(ax, fig)

    if bp_rows:
        dates = [row['date'] for row in bp_rows]
        x_pos = list(range(len(dates)))
        systolic = [row['bp_systolic'] for row in bp_rows]
        diastolic = [row['bp_diastolic'] for row in bp_rows]

        _plot_glow_series(ax, x_pos, systolic, '#22d3ee', 'Systolic', show_slope=True, single_point_baseline=120)
        _plot_glow_series(ax, x_pos, diastolic, '#38bdf8', 'Diastolic', show_slope=True, single_point_baseline=120)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(dates)
        legend = ax.legend(
            facecolor='#121b2d',
            edgecolor='#334155',
            framealpha=0.9,
            loc='upper left'
        )
        for text in legend.get_texts():
            text.set_color('#cbd5e1')
    else:
        ax.text(
            0.5, 0.5, 'No blood pressure data available',
            transform=ax.transAxes,
            ha='center', va='center',
            color='#94a3b8', fontsize=12, fontweight='semibold'
        )
        ax.set_xticks([])
        ax.set_yticks([])

    ax.set_title('Blood Pressure', color='#e2e8f0', fontsize=13, pad=12, fontweight='bold')
    ax.set_xlabel('Date', color='#cbd5e1')
    ax.set_ylabel('mmHg', color='#cbd5e1')
    plt.xticks(rotation=28, ha='right')
    plt.tight_layout()

    buffer = BytesIO()
    plt.savefig(
        buffer,
        format='png',
        dpi=120,
        bbox_inches='tight',
        pad_inches=0.08,
        facecolor=fig.get_facecolor(),
        edgecolor=fig.get_facecolor(),
        transparent=False
    )
    buffer.seek(0)
    plt.close(fig)

    from flask import make_response
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'image/png'
    return response

@app.route("/vitals_svg/<chart_type>")
def vitals_svg(chart_type):
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from io import StringIO
    except ImportError:
        return {"success": False, "message": "Matplotlib not available"}, 500

    conn = get_db()

    if chart_type == "weight":
        rows = conn.execute("""
            SELECT date, weight FROM weight_tracking
            WHERE user_id=? AND weight IS NOT NULL
            ORDER BY date ASC, id ASC
        """, (session["user_id"],)).fetchall()
    elif chart_type == "height":
        rows = conn.execute("""
            SELECT date, height FROM weight_tracking
            WHERE user_id=? AND height IS NOT NULL
            ORDER BY date ASC, id ASC
        """, (session["user_id"],)).fetchall()
    elif chart_type == "bp":
        rows = conn.execute("""
            SELECT date, bp_systolic, bp_diastolic FROM weight_tracking
            WHERE user_id=? AND bp_systolic IS NOT NULL AND bp_diastolic IS NOT NULL
            ORDER BY date ASC, id ASC
        """, (session["user_id"],)).fetchall()
    else:
        conn.close()
        return {"success": False, "message": "Invalid chart type"}, 400

    conn.close()

    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=120)
    _style_vitals_axes(ax, fig)

    if chart_type == "weight":
        if rows:
            dates = [r["date"] for r in rows]
            x_pos = list(range(len(dates)))
            y = [r["weight"] for r in rows]
            _plot_glow_series(ax, x_pos, y, '#22d3ee', show_slope=True, single_point_baseline=0)
            ax.set_xticks(x_pos)
            ax.set_xticklabels(dates)
        else:
            ax.text(0.5, 0.5, 'No weight data available', transform=ax.transAxes,
                    ha='center', va='center', color='#94a3b8', fontsize=12, fontweight='semibold')
            ax.set_xticks([])
            ax.set_yticks([])
        ax.set_ylabel('Weight (kg)', color='#cbd5e1')

    elif chart_type == "height":
        if rows:
            dates = [r["date"] for r in rows]
            x_pos = list(range(len(dates)))
            y = [r["height"] for r in rows]
            _plot_glow_series(ax, x_pos, y, '#2dd4bf', show_slope=True, single_point_baseline=0)
            ax.set_xticks(x_pos)
            ax.set_xticklabels(dates)
        else:
            ax.text(0.5, 0.5, 'No height data available', transform=ax.transAxes,
                    ha='center', va='center', color='#94a3b8', fontsize=12, fontweight='semibold')
            ax.set_xticks([])
            ax.set_yticks([])
        ax.set_ylabel('Height (cm)', color='#cbd5e1')

    else:
        if rows:
            dates = [r["date"] for r in rows]
            x_pos = list(range(len(dates)))
            systolic = [r["bp_systolic"] for r in rows]
            diastolic = [r["bp_diastolic"] for r in rows]
            _plot_glow_series(ax, x_pos, systolic, '#22d3ee', 'Systolic', show_slope=True, single_point_baseline=120)
            _plot_glow_series(ax, x_pos, diastolic, '#38bdf8', 'Diastolic', show_slope=True, single_point_baseline=120)
            ax.set_xticks(x_pos)
            ax.set_xticklabels(dates)
            legend = ax.legend(facecolor='#121b2d', edgecolor='#334155', framealpha=0.9, loc='upper left')
            for t in legend.get_texts():
                t.set_color('#cbd5e1')
        else:
            ax.text(0.5, 0.5, 'No blood pressure data available', transform=ax.transAxes,
                    ha='center', va='center', color='#94a3b8', fontsize=12, fontweight='semibold')
            ax.set_xticks([])
            ax.set_yticks([])
        ax.set_ylabel('mmHg', color='#cbd5e1')

    ax.set_xlabel('Date', color='#cbd5e1')
    plt.xticks(rotation=28, ha='right')
    plt.tight_layout()

    svg_buffer = StringIO()
    plt.savefig(
        svg_buffer,
        format='svg',
        bbox_inches='tight',
        pad_inches=0.08,
        facecolor=fig.get_facecolor(),
        edgecolor=fig.get_facecolor(),
        transparent=False
    )
    plt.close(fig)

    import re
    svg_markup = re.sub(r'<title>.*?</title>', '', svg_buffer.getvalue(), flags=re.DOTALL)

    from flask import make_response
    response = make_response(svg_markup)
    response.headers['Content-Type'] = 'image/svg+xml'
    return response

# ---------------- SERVER-SIDE NOTIFICATIONS ----------------
@app.route("/send_notification", methods=["POST"])
def send_notification():
    if "user_id" not in session:
        return {"success": False, "message": "Unauthorized"}, 401

    message = request.form.get("message")
    notification_type = request.form.get("type", "general")
    target_user_id = request.form.get("target_user_id")

    if not message:
        return {"success": False, "message": "Message is required"}, 400

    conn = get_db()

    # If target_user_id is provided, send to specific user, otherwise send to current user
    user_id = target_user_id if target_user_id else session["user_id"]

    conn.execute("""
        INSERT INTO notifications (user_id, message, type)
        VALUES (?, ?, ?)
    """, (user_id, message, notification_type))

    conn.commit()
    conn.close()

    # Flash message for immediate feedback
    flash("Notification sent successfully!", "success")

    return {"success": True, "message": "Notification sent"}

@app.route("/notifications")
def view_notifications():
    if "user_id" not in session:
        return redirect("/login")

    conn = get_db()
    # Mark patient notifications as seen when page is opened
    conn.execute("""
        UPDATE notifications
        SET read=1
        WHERE user_id=? AND read=0
    """, (session["user_id"],))

    notifications = conn.execute("""
        SELECT * FROM notifications
        WHERE user_id=?
          AND type!='login_alert'
          AND type!='aqi_alert'
        ORDER BY created_at DESC
    """, (session["user_id"],)).fetchall()
    conn.commit()
    conn.close()

    # Use stack (list) for LIFO ordering of notifications
    notification_stack = []
    for notification in notifications:
        notification_stack.append(notification)

    return render_template("notifications.html", notifications=notification_stack)

# ---------------- GET PATIENT PRESCRIPTIONS ----------------
@app.route("/get_patient_prescriptions")
def get_patient_prescriptions():
    if "user_id" not in session or session.get("role") != "user":
        return {"success": False, "message": "Unauthorized"}, 401

    conn = get_db()
    prescriptions = conn.execute("""
        SELECT p.*, a.date as appointment_date, d.fname as doctor_fname, d.lname as doctor_lname
        FROM prescriptions p
        JOIN appointments a ON p.appointment_id = a.id
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.patient_id=? AND a.status='accepted'
        ORDER BY p.created_at DESC
    """, (session["user_id"],)).fetchall()
    conn.close()

    return {"success": True, "prescriptions": [dict(p) for p in prescriptions]}

# ---------------- PATIENT PRESCRIPTIONS PAGE ----------------
@app.route("/patient_prescriptions")
def patient_prescriptions():
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    conn = get_db()
    prescriptions = conn.execute("""
        SELECT p.*, a.date as appointment_date, d.fname as doctor_fname, d.lname as doctor_lname,
               strftime('%Y-%m-%d', p.created_at) as date_key
        FROM prescriptions p
        JOIN appointments a ON p.appointment_id = a.id
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.patient_id=? AND a.status='accepted'
        ORDER BY p.created_at DESC
    """, (session["user_id"],)).fetchall()
    conn.close()

    # Group prescriptions by date
    prescriptions_by_date = {}
    for prescription in prescriptions:
        date = prescription['date_key']
        if date not in prescriptions_by_date:
            prescriptions_by_date[date] = []
        prescriptions_by_date[date].append(dict(prescription))

    return render_template("patient_prescriptions.html", prescriptions_by_date=prescriptions_by_date)

# ---------------- PRESCRIPTION BILL ----------------
def _build_prescription_bill_data(appointment_id, patient_user_id):
    conn = get_db()

    try:
        appointment_check = conn.execute("""
            SELECT id FROM appointments
            WHERE id=? AND patient_id=?
        """, (appointment_id, patient_user_id)).fetchone()

        if not appointment_check:
            return None, "Appointment not found or access denied."

        prescriptions = conn.execute("""
            SELECT p.*, a.date as appointment_date,
                   u.fname as patient_fname, u.lname as patient_lname,
                   d.fname as doctor_fname, d.lname as doctor_lname, d.specialty
            FROM prescriptions p
            JOIN appointments a ON p.appointment_id = a.id
            JOIN users u ON a.patient_id = u.id
            JOIN doctors d ON a.doctor_id = d.id
            WHERE p.appointment_id=?
            ORDER BY p.created_at ASC
        """, (appointment_id,)).fetchall()
    finally:
        conn.close()

    if not prescriptions:
        return None, "No prescriptions found for this appointment."

    medicines = []
    subtotal = 0

    for prescription in prescriptions:
        price = prescription['price'] or 0
        duration = prescription['duration'] or 1
        tablets = prescription['tablets'] or 1
        total = price * duration * tablets

        medicines.append({
            'medicine_name': prescription['medicine_name'],
            'tablets': tablets,
            'price': price,
            'duration': duration,
            'total': total
        })

        subtotal += total

    gst_rate = 18
    gst_amount = subtotal * (gst_rate / 100)
    total_amount = subtotal + gst_amount

    return {
        'patient_name': f"{prescriptions[0]['patient_fname']} {prescriptions[0]['patient_lname']}",
        'doctor_name': f"Dr. {prescriptions[0]['doctor_fname']} {prescriptions[0]['doctor_lname']}",
        'doctor_specialty': prescriptions[0]['specialty'],
        'appointment_date': prescriptions[0]['appointment_date'],
        'medicines': medicines,
        'subtotal': subtotal,
        'gst_rate': gst_rate,
        'gst_amount': gst_amount,
        'total_amount': total_amount
    }, None


def _write_printable_bill_file(appointment_id, bill_data):
    safe_patient = html.escape(bill_data['patient_name'])
    safe_doctor = html.escape(bill_data['doctor_name'])
    safe_specialty = html.escape(str(bill_data['doctor_specialty'] or "N/A"))
    safe_date = html.escape(str(bill_data['appointment_date'] or "N/A"))

    medicine_rows = []
    for medicine in bill_data['medicines']:
        medicine_rows.append(
            "<tr>"
            f"<td>{html.escape(str(medicine['medicine_name'] or ''))}</td>"
            f"<td>{medicine['tablets']}</td>"
            f"<td>{medicine['duration']}</td>"
            f"<td>{medicine['tablets'] * medicine['duration']}</td>"
            f"<td>{medicine['price']:.2f}</td>"
            f"<td>{medicine['total']:.2f}</td>"
            "</tr>"
        )

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prescription Bill #{appointment_id}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      margin: 0;
      color: #111;
      background: #fff;
    }}
    .page {{
      max-width: 1000px;
      margin: 20px auto;
      padding: 16px;
    }}
    h1 {{
      margin: 0 0 16px;
      font-size: 28px;
    }}
    .meta {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-bottom: 16px;
    }}
    .meta-box {{
      border: 1px solid #ccc;
      border-radius: 6px;
      padding: 10px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
    }}
    th, td {{
      border: 1px solid #ccc;
      padding: 8px;
      text-align: left;
      font-size: 14px;
    }}
    th {{
      background: #f5f5f5;
    }}
    .num {{
      text-align: right;
    }}
    .totals {{
      margin-top: 16px;
      margin-left: auto;
      width: 320px;
      border: 1px solid #ccc;
      border-radius: 6px;
      padding: 10px;
    }}
    .row {{
      display: flex;
      justify-content: space-between;
      margin: 6px 0;
    }}
    .grand {{
      font-weight: bold;
      font-size: 16px;
    }}
    .actions {{
      margin-top: 18px;
      display: flex;
      gap: 10px;
    }}
    .btn {{
      border: 1px solid #333;
      background: #fff;
      color: #111;
      padding: 8px 12px;
      border-radius: 4px;
      cursor: pointer;
      text-decoration: none;
      display: inline-block;
    }}
    @page {{
      margin: 12mm;
      size: auto;
    }}
    @media print {{
      .actions {{
        display: none;
      }}
      .page {{
        margin: 0;
        max-width: none;
        padding: 0;
      }}
      thead {{
        display: table-header-group;
      }}
      tr {{
        page-break-inside: avoid;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <h1>Prescription Bill</h1>
    <div class="meta">
      <div class="meta-box">
        <strong>Patient:</strong> {safe_patient}<br>
        <strong>Appointment Date:</strong> {safe_date}
      </div>
      <div class="meta-box">
        <strong>Doctor:</strong> {safe_doctor}<br>
        <strong>Specialty:</strong> {safe_specialty}
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th>Medicine Name</th>
          <th class="num">Tablets/Day</th>
          <th class="num">Duration (Days)</th>
          <th class="num">Quantity</th>
          <th class="num">Price (INR)</th>
          <th class="num">Total (INR)</th>
        </tr>
      </thead>
      <tbody>
        {''.join(medicine_rows)}
      </tbody>
    </table>
    <div class="totals">
      <div class="row"><span>Subtotal</span><span>{bill_data['subtotal']:.2f}</span></div>
      <div class="row"><span>GST ({bill_data['gst_rate']}%)</span><span>{bill_data['gst_amount']:.2f}</span></div>
      <div class="row grand"><span>Total</span><span>{bill_data['total_amount']:.2f}</span></div>
    </div>
    <div class="actions">
      <button class="btn" onclick="window.print()">Print Bill</button>
      <a class="btn" href="/prescription_bill/{appointment_id}">Back to Bill</a>
      <button class="btn" onclick="window.location.href='/prescription_bill/{appointment_id}'">Go Back</button>
    </div>
  </div>
  <script>
    window.addEventListener('load', function () {{
      window.print();
    }});
  </script>
</body>
</html>
"""

    bills_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated_bills")
    os.makedirs(bills_dir, exist_ok=True)
    file_path = os.path.join(bills_dir, f"prescription_bill_{appointment_id}.html")

    with open(file_path, "w", encoding="utf-8") as bill_file:
        bill_file.write(html_doc)

    return file_path


@app.route("/prescription_bill/<int:appointment_id>")
def prescription_bill(appointment_id):
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    bill_data, error = _build_prescription_bill_data(appointment_id, session["user_id"])
    if error:
        flash(error, "warning" if "No prescriptions" in error else "danger")
        return redirect("/patient_prescriptions")

    return render_template("prescription_bill.html", bill=bill_data, appointment_id=appointment_id)


@app.route("/prescription_bill_print/<int:appointment_id>")
def prescription_bill_print(appointment_id):
    if "user_id" not in session or session.get("role") != "user":
        return redirect("/login")

    bill_data, error = _build_prescription_bill_data(appointment_id, session["user_id"])
    if error:
        flash(error, "warning" if "No prescriptions" in error else "danger")
        return redirect("/patient_prescriptions")

    file_path = _write_printable_bill_file(appointment_id, bill_data)
    return send_file(file_path, mimetype="text/html")

# ---------------- SAVE VITALS ----------------
@app.route("/save_vitals/<int:patient_id>", methods=["POST"])
def save_vitals(patient_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return {"success": False, "message": "Unauthorized"}, 401

    conn = get_db()
    try:
        data = request.get_json()
        weight = data.get("weight")
        height = data.get("height")
        bp_systolic = data.get("bp_systolic")
        bp_diastolic = data.get("bp_diastolic")

        if weight is None or height is None or bp_systolic is None or bp_diastolic is None:
            return {"success": False, "message": "All fields are required"}, 400

        from datetime import datetime
        date = datetime.now().strftime('%Y-%m-%d')

        conn.execute("""
            INSERT INTO weight_tracking (user_id, weight, height, bp_systolic, bp_diastolic, date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (patient_id, weight, height, bp_systolic, bp_diastolic, date))
        conn.commit()

        return {"success": True, "message": "Vitals saved successfully"}

    except Exception as e:
        print(f"Error saving vitals: {e}")
        return {"success": False, "message": "Error saving vitals"}, 500
    finally:
        conn.close()

# ---------------- SAVE NOTES ----------------
@app.route("/save_notes/<int:appointment_id>", methods=["POST"])
def save_notes(appointment_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return {"success": False, "message": "Unauthorized"}, 401

    try:
        data = request.get_json()
        notes = data.get("notes")

        if not notes:
            return {"success": False, "message": "Notes are required"}, 400

        conn = get_db()

        # Verify appointment belongs to doctor
        appointment = conn.execute("""
            SELECT id FROM appointments
            WHERE id=? AND doctor_id=(SELECT id FROM doctors WHERE user_id=?)
        """, (appointment_id, session["user_id"])).fetchone()

        if not appointment:
            conn.close()
            return {"success": False, "message": "Appointment not found or access denied"}, 404

        # Insert or update notes
        conn.execute("""
            INSERT OR REPLACE INTO consultation_notes (appointment_id, notes)
            VALUES (?, ?)
        """, (appointment_id, notes))

        conn.commit()
        conn.close()

        return {"success": True, "message": "Notes saved successfully"}

    except Exception as e:
        print(f"Error saving notes: {e}")
        return {"success": False, "message": "Error saving notes"}, 500

# ---------------- GET CONSULTATION NOTES ----------------
@app.route("/get_consultation_notes/<int:appointment_id>")
def get_consultation_notes(appointment_id):
    if "user_id" not in session:
        return {"success": False, "message": "Unauthorized"}, 401

    conn = get_db()

    # Check if user is the patient or the doctor for this appointment
    appointment = conn.execute("""
        SELECT a.*, d.user_id as doctor_user_id
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.id=?
    """, (appointment_id,)).fetchone()

    if not appointment:
        conn.close()
        return {"success": False, "message": "Appointment not found"}, 404

    # Allow access if user is the patient or the doctor
    if session["user_id"] != appointment["patient_id"] and session["user_id"] != appointment["doctor_user_id"]:
        conn.close()
        return {"success": False, "message": "Access denied"}, 403

    # Get consultation notes
    notes = conn.execute("""
        SELECT notes FROM consultation_notes
        WHERE appointment_id=?
    """, (appointment_id,)).fetchone()

    conn.close()

    if notes:
        return {"success": True, "notes": notes["notes"]}
    else:
        return {"success": True, "notes": "No consultation notes available."}

# ---------------- GET PATIENT VITALS ----------------
@app.route("/get_patient_vitals")
def get_patient_vitals():
    if "user_id" not in session or session.get("role") != "user":
        return {"success": False, "message": "Unauthorized"}, 401

    conn = get_db()
    vitals = conn.execute("""
        SELECT * FROM weight_tracking
        WHERE user_id=?
        ORDER BY date ASC
    """, (session["user_id"],)).fetchall()
    conn.close()

    return {"success": True, "vitals": [dict(v) for v in vitals]}

# ---------------- PROFILE PROGRESS CALCULATION ----------------
@app.route("/calculate_profile_progress", methods=["POST"])
def calculate_profile_progress():
    """Calculate doctor profile completion progress based on form data"""
    if "user_id" not in session or session.get("role") != "doctor":
        return {"success": False, "message": "Unauthorized"}, 401
    
    # Get form data from request
    specialty = request.form.get("specialty", "").strip()
    fees = request.form.get("fees", "").strip()
    age = request.form.get("age", "").strip()
    experience_years = request.form.get("experience_years", "").strip()
    qualification = request.form.get("qualification", "").strip()
    license_no = request.form.get("license", "").strip()
    hospital_name = request.form.get("hospital_name", "").strip()
    city = request.form.get("city", "").strip()
    state = request.form.get("state", "").strip()
    landmark = request.form.get("landmark", "").strip()
    full_address = request.form.get("full_address", "").strip()
    pincode = request.form.get("pincode", "").strip()
    latitude = request.form.get("latitude", "").strip()
    longitude = request.form.get("longitude", "").strip()
    staff_count = request.form.get("staff_count", "").strip()
    languages = request.form.get("languages", "").strip()
    reviews = request.form.get("reviews", "").strip()
    awards = request.form.get("awards", "").strip()
    emergency_contact = request.form.get("emergency_contact", "").strip()

    # Calculate progress percentage (same logic as JavaScript)
    percent = 25  # Base percentage

    if specialty: percent += 7  # Specialty is required
    if fees: percent += 7  # Fees is required
    if age: percent += 3
    if experience_years: percent += 3
    if qualification: percent += 7
    if license_no: percent += 7
    if hospital_name: percent += 4
    if city: percent += 4
    if state: percent += 4
    if landmark: percent += 3
    if full_address: percent += 7
    if pincode: percent += 4
    if latitude: percent += 3
    if longitude: percent += 3
    if staff_count: percent += 3
    if languages: percent += 3
    if reviews: percent += 6
    if awards: percent += 6
    if emergency_contact: percent += 5

    percent = min(percent, 100)  # Cap at 100%

    return {"success": True, "progress": percent}

# ---------------- TEST SAVE VITALS ----------------
def test_save_vitals(patient_id, weight, height, bp_systolic, bp_diastolic):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "database.db")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        # Check if patient exists
        patient = conn.execute("SELECT id FROM users WHERE id=?", (patient_id,)).fetchone()
        if not patient:
            return {"success": False, "message": "Patient not found"}

        # Validate data
        if weight is None or height is None or bp_systolic is None or bp_diastolic is None:
            return {"success": False, "message": "All fields are required"}

        date = datetime.now().strftime('%Y-%m-%d')

        conn.execute("""
            INSERT INTO weight_tracking (user_id, weight, height, bp_systolic, bp_diastolic, date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (patient_id, weight, height, bp_systolic, bp_diastolic, date))
        conn.commit()

        return {"success": True, "message": "Vitals saved successfully"}

    except Exception as e:
        print(f"Error saving vitals: {e}")
        return {"success": False, "message": "Error saving vitals"}
    finally:
        conn.close()

# ---------------- RUN ----------------
if __name__ == "__main__":
    # Fix for Windows Unicode encoding issues
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # Test with patient_id 18 (from earlier query)
    result = test_save_vitals(18, 70.5, 170.0, 120, 80)
    print("Test result:", result)

    # Check if vitals were saved
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM weight_tracking')
    count = c.fetchone()[0]
    print(f'Total vitals records after test: {count}')
    c.execute('SELECT * FROM weight_tracking ORDER BY id DESC LIMIT 1')
    row = c.fetchone()
    print("Latest vitals record:", row)
    conn.close()

    app.run(debug=True)


# 🚀 Excited to Present Our Project – MedStash 🏥💻



# MedStash, a complete healthcare management platform developed by me along with my teammates Heer Hirpara and Nidhi.

# MedStash digitally connects patients and doctors while managing appointments, billing, prescriptions, reminders, and medical records — all in one centralized system.



# 👨‍⚕️ Doctor Dashboard

# • View upcoming appointments

# • Access appointment history

# • Manage appointments

# • Notification panel

# • Digital prescription management

# • Access patient medical records

# • Billing management

# • Wallet integration

# • Complete consultation tracking 



# 👩‍💻 Patient Dashboard

# • Choose from multiple doctors & specialties

# • Book appointments easily

# • View upcoming appointments

# • Access appointment history

# • Automatic bill generation after booking

# • View & manage digital medical records

# • Track health charts 

# • View billing history

# • Download & print bills



# 📊 System Highlights

# • Role-based access control

# • Secure data handling

# • Centralized healthcare workflow

# • End-to-end doctor & patient management



# This project helped us strengthen our skills in system design, dashboard development, and building scalable healthcare solutions. Building this project significantly enhanced my problem-solving abilities, my understanding of backend development using Flask, database management with SQLite, system design concepts, and real-world healthcare system workflows.

# I’m open to feedback and collaboration opportunities!

# MedStash



#  #Flask #SQLite #PythonDevelopment #HealthcareTechnology #HealthTech  #HospitalManagementSystem #WebDevelopment #TechProject #TeamWork #DigitalHealth #SoftwareDevelopment