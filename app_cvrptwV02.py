import requests
import math
import pandas as pd
import streamlit as st
import folium
from folium.plugins import AntPath
from streamlit_folium import st_folium
from solver import solve_cvrptw

# -------------------------------
# Session state initialization
# -------------------------------
if "optimization_result" not in st.session_state:
    st.session_state.optimization_result = None
if "optimization_data" not in st.session_state:
    st.session_state.optimization_data = None
if "optimization_metrics" not in st.session_state:
    st.session_state.optimization_metrics = None

# --- PAGE CONFIG ---
st.set_page_config(page_title="CV Indra Jaya Shoes Distribution DSS", layout="wide", page_icon="👟")

# --- HEADER ---
st.title("Shoes Distribution Route Optimization System")
st.markdown("**Case Study:** CV Indra Jaya CVRPTW Model (Updated Locations)")

# -------------------------------
# OSRM helpers
# -------------------------------
def get_osrm_matrices(data):
    coord_string = ";".join([f"{lon},{lat}" for lat, lon in data["raw_coords"]])
    url = f"http://router.project-osrm.org/table/v1/driving/{coord_string}?annotations=duration,distance"
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    result = response.json()
    
    if result.get("code") != "Ok":
        raise ValueError(f"OSRM returned an error: {result}")
    
    data["distance_matrix"] = [[int(v) if v is not None else 999999999 for v in row] for row in result["distances"]]
    data["time_matrix"] = [[math.ceil(v/60) if v is not None else 999999 for v in row] for row in result["durations"]]
    return data

@st.cache_data(show_spinner=False)
def get_osrm_route_geometry(start_coord, end_coord):
    start_lat, start_lon = start_coord
    end_lat, end_lon = end_coord
    url = f"http://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview=full&geometries=geojson&steps=false"
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    result = response.json()
    if result.get("code") != "Ok":
        return [start_coord, end_coord], 0
    geometry = result["routes"][0]["geometry"]["coordinates"]
    return [(lat, lon) for lon, lat in geometry], None

def get_full_osrm_route(route):
    all_points = []
    for i in range(len(route["Coordinates"]) - 1):
        segment_points, _ = get_osrm_route_geometry(route["Coordinates"][i], route["Coordinates"][i+1])
        if i > 0 and len(segment_points) > 0:
            segment_points = segment_points[1:]
        all_points.extend(segment_points)
    return all_points, None

def create_enhanced_route_map(routes):
    route_colors = ["#FF0000", "#0000FF", "#008000", "#FFA500", "#800080", "#FF00FF", "#00FFFF"]
    
    valid_routes = [r for r in routes if r.get("Coordinates") and len(r["Coordinates"]) > 1]
    if not valid_routes:
        fallback_map = folium.Map(location=[-6.60315, 106.76218], zoom_start=11)
        return fallback_map

    combined_map = folium.Map(location=valid_routes[0]["Coordinates"][0], zoom_start=11)
    for route in valid_routes:
        vehicle_color = route_colors[(route["Vehicle"]-1) % len(route_colors)]
        road_points, _ = get_full_osrm_route(route)
        if road_points:
            AntPath(locations=road_points, color=vehicle_color, weight=6, opacity=0.9, delay=800).add_to(combined_map)
        
        for idx, stop in enumerate(route["Schedule"]):
            tw_start = stop.get('Opening_Minutes', 0)
            tw_end = stop.get('Deadline_Minutes', 1439)
            tw_text = f"{tw_start//60:02d}:{tw_start%60:02d} - {tw_end//60:02d}:{tw_end%60:02d}"

            folium.Marker(
                location=[stop["Latitude"], stop["Longitude"]],
                tooltip=f"Vehicle {route['Vehicle']} - Stop {idx+1}",
                popup=(
                    f"<b>{stop['Location']}</b><br>"
                    f"Operational Window: {tw_text}<br>"
                    f"Arrival Time: {stop.get('Time','N/A')}<br>"
                    f"Demand: {stop.get('Demand',0)}<br>"
                    f"Vehicle Utilization: {route.get('Utilization (%)',0)}%<br>"
                    f"Distance: {route.get('Distance (km)',0)} km<br>"
                    f"Lateness: {stop.get('Lateness_Minutes', 0)} min"
                ),
                icon=folium.Icon(color="blue" if idx > 0 and idx < len(route["Schedule"])-1 else "red", icon="info-sign")
            ).add_to(combined_map)
    return combined_map

def compute_vehicle_metrics(result, data):
    name_to_index = {name: idx for idx, name in enumerate(data["address_list"])}
    
    for route in result["route_results"]:
        schedule = route["Schedule"]
        delivered = sum(stop.get("Demand", 0) for stop in schedule)
        route_capacity = data["vehicle_capacities"][route["Vehicle"]-1]
        route["Utilization (%)"] = round((delivered / route_capacity) * 100, 1) if route_capacity > 0 else 0
        
        route_distance_m = 0
        for i in range(len(schedule) - 1):
            from_idx = name_to_index[schedule[i]["Location"]]
            to_idx = name_to_index[schedule[i+1]["Location"]]
            route_distance_m += data["distance_matrix"][from_idx][to_idx]
        route["Distance (km)"] = round(route_distance_m / 1000, 2)
        
        if schedule:
            total_lateness = 0
            for stop in schedule:
                arrival = stop.get("Arrival_Minutes", 0)
                deadline = stop.get("Deadline_Minutes", 1439)
                lateness = max(0, arrival - deadline)
                stop["Lateness_Minutes"] = lateness
                total_lateness += lateness
            route["Lateness (min)"] = total_lateness
        else:
            route["Lateness (min)"] = 0
            
    return result

# -------------------------------
# Sidebar inputs (Global parameters)
# -------------------------------
st.sidebar.header("⚙️ Operational Scenarios")
scenario = st.sidebar.selectbox("Select Scenario", ["Normal distribution day", "Peak distribution day", "Delayed departure"])

st.sidebar.header("🚚 Fleet Parameters")
fuel_cost_per_km = st.sidebar.number_input("Fuel Cost per KM", 1000, 50000, 5000)
driver_cost_per_vehicle = st.sidebar.number_input("Driver Cost per Vehicle", 10000, 500000, 50000)
num_vehicles = st.sidebar.number_input("Number of Vehicles", 1, 15, 5)
vehicle_capacity = st.sidebar.number_input("Vehicle Capacity (Cartons/Pairs)", 50, 10000, 2500)

# -------------------------------
# Dynamic Sidebar Location Controls (Demand & Time Windows)
# -------------------------------
st.sidebar.header("📦 Location Custom Configurations")

locations_metadata = [
    {"name": "Depot", "def_demand": 0, "def_open": 0, "def_close": 23},
    {"name": "Tangerang", "def_demand": 2000, "def_open": 8, "def_close": 17},
    {"name": "Pejaten", "def_demand": 220, "def_open": 9, "def_close": 21},
    {"name": "Central Park", "def_demand": 195, "def_open": 10, "def_close": 22},
    {"name": "Cikarang", "def_demand": 100, "def_open": 8, "def_close": 17},
    {"name": "Karawaci", "def_demand": 80, "def_open": 10, "def_close": 22},
    {"name": "Cibinong", "def_demand": 55, "def_open": 8, "def_close": 17},
    {"name": "Cibubur", "def_demand": 80, "def_open": 9, "def_close": 19},
    {"name": "Pondok Indah Mall 2", "def_demand": 80, "def_open": 10, "def_close": 22},
    {"name": "Casablanca", "def_demand": 95, "def_open": 10, "def_close": 22},
    {"name": "Alam Sutera", "def_demand": 130, "def_open": 10, "def_close": 22},
    {"name": "Depok", "def_demand": 100, "def_open": 8, "def_close": 20},
    {"name": "Sudirman", "def_demand": 110, "def_open": 8, "def_close": 17},
    {"name": "Plaza Indonesia", "def_demand": 200, "def_open": 10, "def_close": 22},
    {"name": "Bintaro", "def_demand": 170, "def_open": 9, "def_close": 20},
    {"name": "Bogor", "def_demand": 1000, "def_open": 8, "def_close": 17},
    {"name": "Ciomas", "def_demand": 400, "def_open": 8, "def_close": 17}
]

user_demands = []
user_time_windows = []

# Construct inputs inside sidebar components matching the design preference
for loc in locations_metadata:
    with st.sidebar.expander(f"📍 {loc['name']}", expanded=False):
        if loc["name"] != "Depot":
            demand_input = st.number_input(f"Demand", 0, 5000, loc["def_demand"], key=f"d_in_{loc['name']}")
        else:
            demand_input = 0
            st.caption("Depot load defaults to 0.")
            
        col_start, col_end = st.columns(2)
        open_hour = col_start.number_input("Open (0-23)", 0, 23, loc["def_open"], key=f"o_hr_{loc['name']}")
        close_hour = col_end.number_input("Close (0-23)", 0, 23, loc["def_close"], key=f"c_hr_{loc['name']}")
        
        # Enforce chronological validation safety limits
        if open_hour > close_hour:
            st.error("Open hour cannot be after close hour.")
            close_hour = open_hour
            
        start_minutes = open_hour * 60
        end_minutes = (close_hour * 60) + 59  # Capture the full duration of the final active hour hour loop
        
        user_demands.append(demand
