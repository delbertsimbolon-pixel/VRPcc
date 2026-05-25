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
st.set_page_config(page_title="Gunadarma Exam Distribution DSS", layout="wide", page_icon="📦")

# --- HEADER ---
st.title("Examination Document Distribution Route Optimization System")
st.markdown("**Case Study:** Universitas Gunadarma CVRPTW Model (Updated Locations)")

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
    combined_map = folium.Map(location=routes[0]["Coordinates"][0], zoom_start=11)
    for route in routes:
        vehicle_color = route_colors[(route["Vehicle"]-1) % len(route_colors)]
        road_points, _ = get_full_osrm_route(route)
        AntPath(locations=road_points, color=vehicle_color, weight=6, opacity=0.9, delay=800).add_to(combined_map)
        for idx, stop in enumerate(route["Schedule"]):
            folium.Marker(
                location=[stop["Latitude"], stop["Longitude"]],
                tooltip=f"Vehicle {route['Vehicle']} - Stop {idx+1}",
                popup=(
                    f"<b>{stop['Location']}</b><br>"
                    f"Arrival: {stop.get('Time','N/A')}<br>"
                    f"Demand: {stop.get('Demand',0)}<br>"
                    f"Vehicle Utilization: {route.get('Utilization (%)',0)}%<br>"
                    f"Distance: {route.get('Distance (km)',0)} km<br>"
                    f"Lateness: {route.get('Lateness (min)',0)} min"
                ),
                icon=folium.Icon(color="blue", icon="info-sign")
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
            latest_arrival = max([stop.get("Arrival_Minutes", 0) for stop in schedule])
            deadline = max([stop.get("Deadline_Minutes", 1439) for stop in schedule])
            route["Lateness (min)"] = max(0, latest_arrival - deadline)
        else:
            route["Lateness (min)"] = 0
            
    return result

# -------------------------------
# Sidebar inputs
# -------------------------------
st.sidebar.header("⚙️ Operational Scenarios")
scenario = st.sidebar.selectbox("Select Scenario", ["Normal examination day", "Peak examination day", "Delayed departure"])

st.sidebar.header("🚚 Fleet Parameters")
fuel_cost_per_km = st.sidebar.number_input("Fuel Cost per KM", 1000, 50000, 5000)
driver_cost_per_vehicle = st.sidebar.number_input("Driver Cost per Vehicle", 10000, 500000, 50000)
num_vehicles = st.sidebar.number_input("Number of Vehicles", 1, 15, 5)
vehicle_capacity = st.sidebar.number_input("Vehicle Capacity (Packages)", 50, 10000, 2500)

st.sidebar.header("📦 Base Demand Controls")
demand_tangerang = st.sidebar.number_input("Tangerang", 0, 5000, 2000)
demand_pejaten = st.sidebar.number_input("Pejaten", 0, 5000, 220)
demand_central_park = st.sidebar.number_input("Central Park", 0, 5000, 195)
demand_cikarang = st.sidebar.number_input("Cikarang", 0, 5000, 100)
demand_karawaci = st.sidebar.number_input("Karawaci", 0, 5000, 80)
demand_cibinong = st.sidebar.number_input("Cibinong", 0, 5000, 55)
demand_cibubur = st.sidebar.number_input("Cibubur", 0, 5000, 80)
demand_pimm2 = st.sidebar.number_input("Pondok Indah Mall 2", 0, 5000, 80)
demand_casablanca = st.sidebar.number_input("Casablanca", 0, 5000, 95)
demand_alam_sutera = st.sidebar.number_input("Alam Sutera", 0, 5000, 130)
demand_depok = st.sidebar.number_input("Depok", 0, 5000, 100)
demand_sudirman = st.sidebar.number_input("Sudirman", 0, 5000, 110)
demand_plaza_indo = st.sidebar.number_input("Plaza Indonesia", 0, 5000, 200)
demand_bintaro = st.sidebar.number_input("Bintaro", 0, 5000, 170)
demand_bogor = st.sidebar.number_input("Bogor", 0, 5000, 1000)
demand_ciomas = st.sidebar.number_input("Ciomas", 0, 5000, 400)

# -------------------------------
# Run solver
# -------------------------------
if st.button("🚀 Run Route Optimization"):
    multiplier = 1.0
    if scenario == "Peak examination day":
        multiplier = 1.25

    data = {
        "address_list": [
            "Depot", "Tangerang", "Pejaten", "Central Park", "Cikarang", 
            "Karawaci", "Cibinong", "Cibubur", "Pondok Indah Mall 2", "Casablanca", 
            "Alam Sutera", "Depok", "Sudirman", "Plaza Indonesia", "Bintaro", "Bogor", "Ciomas"
        ],
        "raw_coords": [
            (-6.60315, 106.76218),   
            (-6.3353364, 106.68034), 
            (-6.286292, 106.81204),  
            (-6.171083, 106.787784), 
            (-6.333997, 107.13689),  
            (-6.213054, 106.82072),  
            (-6.484245, 106.84319),  
            (-6.375656, 106.90173),  
            (-6.145488, 106.89176),  
            (-6.176046, 106.721175), 
            (-6.237069, 106.65915),  
            (-6.380091, 106.84468),  
            (-6.224799, 106.80397),  
            (-6.194143, 106.82254),  
            (-6.285583, 106.72799),  
            (-6.616831, 106.82188),  
            (-6.6013858, 106.75367)  
        ],
        "demands": [
            0,
            math.ceil(demand_tangerang * multiplier),
            math.ceil(demand_pejaten * multiplier),
            math.ceil(demand_central_park * multiplier),
            math.ceil(demand_cikarang * multiplier),
            math.ceil(demand_karawaci * multiplier),
            math.ceil(demand_cibinong * multiplier),
            math.ceil(demand_cibubur * multiplier),
            math.ceil(demand_pimm2 * multiplier),
            math.ceil(demand_casablanca * multiplier),
            math.ceil(demand_alam_sutera * multiplier),
            math.ceil(demand_depok * multiplier),
            math.ceil(demand_sudirman * multiplier),
            math.ceil(demand_plaza_indo * multiplier),
            math.ceil(demand_bintaro * multiplier),
            math.ceil(demand_bogor * multiplier),
            math.ceil(demand_ciomas * multiplier)
        ],
        "vehicle_capacities": [vehicle_capacity] * num_vehicles,
        "num_vehicles": num_vehicles,
        "depot": 0,
        "depot_start": 0,              
        "time_windows": [(0, 1439)] * 17, 
        "service_times": [0, 6, 6, 6, 3, 6, 6, 3, 5, 5, 6, 6, 4, 6, 6, 3, 3], 
        "fuel_cost_per_km": fuel_cost_per_km,
        "driver_cost_per_vehicle": driver_cost_per_vehicle
    }

    with st.spinner("Fetching matrix configurations and solving..."):
        data = get_osrm_matrices(data)
        result = solve_cvrptw(data)
        
    if result is None:
        st.error("No feasible solution found with current configurations.")
        st.stop()

    st.session_state.optimization_result = compute_vehicle_metrics(result, data)
    st.session_state.optimization_data = data

# -------------------------------
# Display results
# -------------------------------
if st.session_state.optimization_result:
    result = st.session_state.optimization_result
    data = st.session_state.optimization_data
    routes = result["route_results"]

    # --- Top summary box ---
    baseline_distance = 150.0
    optimized_distance = sum(r.get("Distance (km)", 0) for r in routes)
    improvement = ((baseline_distance - optimized_distance) / baseline_distance) * 100 if baseline_distance > 0 else 0
    
    col1, col2, col3, col4, col5 = st.columns(5)
    total_operational_cost = sum(r.get("Total Cost", 0) for r in routes)
    total_packages = sum(r.get('Delivered Packages', 0) for r in routes)
    total_capacity = sum(data['vehicle_capacities'])
    
    col1.metric("Baseline Distance", f"{baseline_distance:.2f} km")
    col2.metric("Optimized Distance", f"{optimized_distance:.2f} km", f"{improvement:.2f}% improvement")
    col3.metric("Total Delivered", f"{total_packages} units")
    col4.metric("Fleet Utilization", f"{(total_packages / total_capacity * 100) if total_capacity > 0 else 0:.1f}%")
    col5.metric("Total Operational Cost", f"Rp {total_operational_cost:,.0f}")
    
    # --- Combined map ---
    st.subheader("🗺️ Combined Route Map")
    st_folium(create_enhanced_route_map(routes), width=1000, height=500)

    # --- Vehicle summary table ---
    st.subheader("🚛 Vehicle Summary Table")
    
    for r in routes:
        for col_name in ["Fuel Cost", "Driver Cost", "Total Cost"]:
            if col_name not in r:
                r[col_name] = 0
                
    vehicle_summary_df = pd.DataFrame(routes)[[
        "Vehicle",
        "Distance (km)",
        "Delivered Packages",
        "Utilization (%)",
        "Fuel Cost",
        "Driver Cost",
        "Total Cost",
        "Lateness (min)"
    ]]
    
    st.dataframe(vehicle_summary_df, use_container_width=True)

    # --- Per-vehicle sections ---
    for route in routes:
        st.markdown(f"### Vehicle {route['Vehicle']}")
        if route.get("Delivered Packages", 0) == 0:
            st.info("Vehicle not needed for this configuration.")
            continue

        with st.expander(f"Vehicle {route['Vehicle']} Map", expanded=False):
            st_folium(create_enhanced_route_map([route]), width=1000, height=450)

        # UPDATED: Renamed column to Location and added Latitude + Longitude columns
        stop_df = pd.DataFrame(route["Schedule"])[["Location", "Latitude", "Longitude", "Time", "Demand"]]
        stop_df["Lateness (min)"] = [max(0, s.get("Arrival_Minutes", 0) - s.get("Deadline_Minutes", 1439)) for s in route["Schedule"]]
        
        st.markdown(f"#### Stop-Level Delivery Table (Vehicle {route['Vehicle']})")
        st.dataframe(stop_df, use_container_width=True)

        csv_bytes = stop_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label=f"📥 Download CSV Vehicle {route['Vehicle']}", 
            data=csv_bytes,
            file_name=f"vehicle_{route['Vehicle']}_stops.csv", 
            mime="text/csv"
        )
