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
st.markdown("**Case Study:** Universitas Gunadarma CVRPTW Model")

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
    # real distance & time matrices
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
    route_colors = ["#FF0000","#0000FF","#008000","#FFA500","#800080"]
    combined_map = folium.Map(location=routes[0]["Coordinates"][0], zoom_start=13)
    for route in routes:
        vehicle_color = route_colors[(route["Vehicle"]-1)%len(route_colors)]
        road_points, _ = get_full_osrm_route(route)
        AntPath(locations=road_points, color=vehicle_color, weight=6, opacity=0.9, delay=800).add_to(combined_map)
        for idx, stop in enumerate(route["Schedule"]):
            folium.Marker(
                location=[stop["Latitude"], stop["Longitude"]],
                tooltip=f"Vehicle {route['Vehicle']} - Stop {idx+1}",
                popup=(
                    f"<b>{stop['Campus']}</b><br>"
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
    for route in result["route_results"]:
        delivered = sum(stop.get("Demand",0) for stop in route["Schedule"])
        route_capacity = data["vehicle_capacities"][route["Vehicle"]-1]
        route["Utilization (%)"] = round((delivered/route_capacity)*100,1)
        route_distance_m = 0
        coords = route["Coordinates"]
        for i in range(len(coords)-1):
            route_distance_m += data["distance_matrix"][i][i+1]
        route["Distance (km)"] = round(route_distance_m/1000,2)
        latest_arrival = max([stop.get("Arrival_Minutes",0) for stop in route["Schedule"]])
        deadline = max([stop.get("Deadline_Minutes",450) for stop in route["Schedule"]])
        route["Lateness (min)"] = max(0, latest_arrival-deadline)
    return result

# -------------------------------
# Sidebar inputs
# -------------------------------
st.sidebar.header("⚙️ Operational Scenarios")
scenario = st.sidebar.selectbox("Select Scenario", ["Normal examination day","Peak examination day","Delayed departure"])
st.sidebar.header("🚚 Fleet Parameters")
num_vehicles = st.sidebar.number_input("Number of Vehicles", 1, 10, 3)
vehicle_capacity = st.sidebar.number_input("Vehicle Capacity (Packages)", 50, 500, 100)
st.sidebar.header("📦 Base Demand Input")
demand_D = st.sidebar.number_input("Campus D", 0, 500, 48)
demand_G = st.sidebar.number_input("Campus G", 0, 500, 30)
demand_F4 = st.sidebar.number_input("Campus F4", 0, 500, 18)
demand_F5 = st.sidebar.number_input("Campus F5", 0, 500, 12)
demand_F6 = st.sidebar.number_input("Campus F6", 0, 500, 15)
demand_F7 = st.sidebar.number_input("Campus F7", 0, 500, 10)
demand_F8 = st.sidebar.number_input("Campus F8", 0, 500, 6)

# -------------------------------
# Run solver
# -------------------------------
if st.button("🚀 Run Route Optimization"):
    multiplier = 1.0
    if scenario=="Peak examination day":
        multiplier = 1.25
    elif scenario=="Delayed departure":
        multiplier = 1.0

    data = {
        "address_list": ["Campus E (Depot)","Campus D","Campus G","Campus F4","Campus F5","Campus F6","Campus F7","Campus F8"],
        "raw_coords": [(-6.353752,106.841593),(-6.367957,106.833096),(-6.354235,106.843384),
                       (-6.373650,106.863186),(-6.369296,106.836768),(-6.345757,106.854354),
                       (-6.344363,106.883077),(-6.369801,106.839587)],
        "demands": [
            0, math.ceil(demand_D*multiplier), math.ceil(demand_G*multiplier),
            math.ceil(demand_F4*multiplier), math.ceil(demand_F5*multiplier),
            math.ceil(demand_F6*multiplier), math.ceil(demand_F7*multiplier),
            math.ceil(demand_F8*multiplier)
        ],
        "vehicle_capacities": [vehicle_capacity]*num_vehicles,
        "num_vehicles": num_vehicles,
        "depot": 0,
        "depot_start": 360,
        "time_windows": [(360,510)] + [(390,450)]*7,
        "service_times": [15,15,10,10,10,10,10,10]
    }

    data = get_osrm_matrices(data)

    result = solve_cvrptw(data)
    if result is None:
        st.error("No feasible solution found")
        st.stop()

    st.session_state.optimization_result = result
    st.session_state.optimization_data = data

# -------------------------------
# Display results
# -------------------------------
if st.session_state.optimization_result:
    result = st.session_state.optimization_result
    data = st.session_state.optimization_data

    st.session_state.optimization_result = compute_vehicle_metrics(result, data)
    routes = st.session_state.optimization_result["route_results"]

    # --- Top summary box ---
    baseline_distance = 50
    optimized_distance = sum(r.get("Distance (km)",0) for r in routes)
    improvement = ((baseline_distance - optimized_distance)/baseline_distance)*100
    col1,col2,col3,col4 = st.columns(4)
    col1.metric("Baseline Distance", f"{baseline_distance:.2f} km")
    col2.metric("Optimized Distance", f"{optimized_distance:.2f} km", f"{improvement:.2f}% improvement")
    col3.metric("Total Delivered", f"{sum(r.get('Delivered Packages',0) for r in routes)} packages")
    col4.metric("Fleet Utilization", f"{sum(r.get('Delivered Packages',0) for r in routes)/sum(data['vehicle_capacities'])*100:.1f}%")

    # --- Combined map ---
    st.subheader("🗺️ Combined Route Map")
    st_folium(create_enhanced_route_map(routes), width=1000, height=500)

    # --- Vehicle summary table ---
    st.subheader("🚛 Vehicle Summary Table")
    vehicle_summary_df = pd.DataFrame(routes)[
        ["Vehicle","Distance (km)","Delivered Packages","Utilization (%)","Lateness (min)"]
    ]
    st.dataframe(vehicle_summary_df,use_container_width=True)

    # --- Per-vehicle sections ---
    for route in routes:
        st.markdown(f"### Vehicle {route['Vehicle']}")
        if route.get("Delivered Packages",0) == 0:
            st.info("Vehicle not needed for this scenario.")
            continue

        with st.expander(f"Vehicle {route['Vehicle']} Map", expanded=True):
            st_folium(create_enhanced_route_map([route]), width=1000, height=450)

        stop_df = pd.DataFrame(route["Schedule"])[["Campus","Time","Demand"]]
        stop_df["Lateness (min)"] = [max(0, s.get("Arrival_Minutes",0)-s.get("Deadline_Minutes",450)) for s in route["Schedule"]]
        st.markdown(f"#### Stop-Level Delivery Table (Vehicle {route['Vehicle']})")
        st.dataframe(stop_df,use_container_width=True)

        csv_bytes = stop_df.to_csv(index=False).encode("utf-8")
        st.download_button(f"📥 Download CSV Vehicle {route['Vehicle']}", csv_bytes,
                           file_name=f"vehicle_{route['Vehicle']}_stops.csv", mime="text/csv")
