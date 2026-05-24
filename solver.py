from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp


def solve_cvrptw(data):
    """
    Solve CVRPTW for examination document distribution.

    Important:
    - Time inside solver is converted into relative minutes from depot_start.
    - This matches app_cvrptwV01.py, which adds depot_start again when displaying time.
    - Example:
        depot_start = 360  # 06:00
        solver arrival = 30
        displayed time = 360 + 30 = 390 = 06:30
    """

    depot = data["depot"]
    depot_start = data.get("depot_start", data["time_windows"][depot][0])

    # Convert absolute time windows into relative time windows
    # Example: site time window 06:30-07:30 = 390-450
    # If depot_start = 360, relative window becomes 30-90
    relative_time_windows = []
    for start, end in data["time_windows"]:
        relative_start = max(0, start - depot_start)
        relative_end = max(0, end - depot_start)
        relative_time_windows.append((relative_start, relative_end))

    # Make sure depot starts at 0
    depot_end_relative = max(0, data["time_windows"][depot][1] - depot_start)
    relative_time_windows[depot] = (0, depot_end_relative)

    manager = pywrapcp.RoutingIndexManager(
        len(data["distance_matrix"]),
        data["num_vehicles"],
        depot
    )

    routing = pywrapcp.RoutingModel(manager)

    # -------------------------------
    # Distance callback
    # -------------------------------
    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(data["distance_matrix"][from_node][to_node])

    distance_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(distance_callback_index)

    # -------------------------------
    # Demand callback
    # -------------------------------
    def demand_callback(from_index):
        from_node = manager.IndexToNode(from_index)
        return int(data["demands"][from_node])

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)

    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,
        data["vehicle_capacities"],
        True,
        "Capacity"
    )

    # -------------------------------
    # Time callback
    # -------------------------------
    def time_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)

        travel_time = int(data["time_matrix"][from_node][to_node])
        service_time = int(data["service_times"][from_node])

        return travel_time + service_time

    time_callback_index = routing.RegisterTransitCallback(time_callback)

    max_time_horizon = max(end for _, end in relative_time_windows)

    routing.AddDimension(
        time_callback_index,
        30,                 # waiting/slack time
        max_time_horizon,   # route time horizon in relative minutes
        False,
        "Time"
    )

    time_dimension = routing.GetDimensionOrDie("Time")

    # -------------------------------
    # Apply time windows
    # -------------------------------
    for location_idx, time_window in enumerate(relative_time_windows):
        index = manager.NodeToIndex(location_idx)
        time_dimension.CumulVar(index).SetRange(time_window[0], time_window[1])

    # -------------------------------
    # Depot time window
    # -------------------------------
    depot_time_window = relative_time_windows[depot]

    for vehicle_id in range(data["num_vehicles"]):
        start_index = routing.Start(vehicle_id)
        end_index = routing.End(vehicle_id)

        time_dimension.CumulVar(start_index).SetRange(
            depot_time_window[0],
            depot_time_window[1]
        )

        time_dimension.CumulVar(end_index).SetRange(
            depot_time_window[0],
            depot_time_window[1]
        )

        routing.AddVariableMinimizedByFinalizer(
            time_dimension.CumulVar(start_index)
        )

        routing.AddVariableMinimizedByFinalizer(
            time_dimension.CumulVar(end_index)
        )

    # -------------------------------
    # Search parameters
    # -------------------------------
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()

    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )

    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )

    search_parameters.time_limit.seconds = 5

    solution = routing.SolveWithParameters(search_parameters)

    if solution is None:
        return None

    # -------------------------------
    # Extract solution
    # -------------------------------
    route_results = []
    stop_results = []
    total_distance = 0
    latest_actual_arrival = 0
    active_vehicles = 0
    display_vehicle_id = 0

    for vehicle_id in range(data["num_vehicles"]):
        index = routing.Start(vehicle_id)

        route_distance = 0
        route_load = 0
        route_nodes = []
        route_schedule = []
        route_node_indices = []
        temporary_stop_results = []

        while not routing.IsEnd(index):
            node_index = manager.IndexToNode(index)
            arrival_time = solution.Min(time_dimension.CumulVar(index))

            route_load += int(data["demands"][node_index])
            route_nodes.append(data["address_list"][node_index])
            route_node_indices.append(node_index)

            absolute_arrival = arrival_time + depot_start
            absolute_deadline = data["time_windows"][node_index][1]

            route_schedule.append({
                "Campus": data["address_list"][node_index],
                "Time": f"{absolute_arrival // 60:02d}:{absolute_arrival % 60:02d}",
                "Arrival_Minutes": arrival_time,
                "Deadline_Minutes": absolute_deadline,
                "Demand": int(data["demands"][node_index]),
                "Latitude": data["raw_coords"][node_index][0],
                "Longitude": data["raw_coords"][node_index][1],
                "Stop Type": "Depot" if node_index == depot else "Delivery"
            })

            if node_index != depot:
                latest_actual_arrival = max(latest_actual_arrival, arrival_time)

                latest_allowed_relative = relative_time_windows[node_index][1]
                lateness = max(0, arrival_time - latest_allowed_relative)

                temporary_stop_results.append({
                    "Campus": data["address_list"][node_index],
                    "Arrival Time": f"{absolute_arrival // 60:02d}:{absolute_arrival % 60:02d}",
                    "Deadline": f"{absolute_deadline // 60:02d}:{absolute_deadline % 60:02d}",
                    "Demand": int(data["demands"][node_index]),
                    "On-Time Status": "On time" if lateness == 0 else "Late",
                    "Lateness (mins)": lateness
                })

            previous_index = index
            index = solution.Value(routing.NextVar(index))

            route_distance += routing.GetArcCostForVehicle(
                previous_index,
                index,
                vehicle_id
            )

        # -------------------------------
        # Return to depot
        # -------------------------------
        end_node = manager.IndexToNode(index)
        end_time = solution.Min(time_dimension.CumulVar(index))
        absolute_end_time = end_time + depot_start

        route_nodes.append(data["address_list"][end_node])
        route_node_indices.append(end_node)

        # FIX: use end_node and end_time, not the last delivery node
        route_schedule.append({
            "Campus": data["address_list"][end_node],
            "Time": f"{absolute_end_time // 60:02d}:{absolute_end_time % 60:02d}",
            "Arrival_Minutes": end_time,
            "Deadline_Minutes": data["time_windows"][end_node][1],
            "Demand": int(data["demands"][end_node]),
            "Latitude": data["raw_coords"][end_node][0],
            "Longitude": data["raw_coords"][end_node][1],
            "Stop Type": "Return to Depot"
        })

        # Only save active vehicles
        if route_load > 0:
            active_vehicles += 1
            display_vehicle_id += 1
            total_distance += route_distance

            for stop in temporary_stop_results:
                stop["Vehicle"] = display_vehicle_id
                stop_results.append(stop)

            route_results.append({
                "Vehicle": display_vehicle_id,
                "Original Vehicle ID": vehicle_id + 1,
                "Route": " -> ".join(route_nodes),
                "Distance (km)": round(route_distance / 1000, 2),
                "Delivered Packages": route_load,
                "Utilization (%)": round(
                    route_load / data["vehicle_capacities"][vehicle_id] * 100,
                    2
                ),
                "Exposure Time (mins)": end_time,
                "Return Time": f"{absolute_end_time // 60:02d}:{absolute_end_time % 60:02d}",
                "Schedule": route_schedule,
                "Node Indices": route_node_indices,
                "Coordinates": [
                    data["raw_coords"][i] for i in route_node_indices
                ]
            })

    return {
        "route_results": route_results,
        "stop_results": stop_results,
        "optimized_distance_km": total_distance / 1000,
        "latest_actual_arrival": latest_actual_arrival,
        "active_vehicles": active_vehicles
    }
