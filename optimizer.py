import math
import heapq
from typing import List, Dict, Tuple, Optional

EARTH_RADIUS_KM = 6371.0088


def haversine(a: Dict, b: Dict) -> float:
    """Great-circle distance between two {lat,lng} objects in km."""
    lat1, lon1 = math.radians(float(a['lat'])), math.radians(float(a['lng']))
    lat2, lon2 = math.radians(float(b['lat'])), math.radians(float(b['lng']))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def node_key(node: Dict, fallback: str) -> str:
    return str(node.get('uid') or node.get('id') or fallback)


def build_complete_graph(nodes: List[Dict], blocked_edges: Optional[List[Dict]] = None):
    """Build a weighted graph and optionally remove user-simulated links."""
    graph = {i: [] for i in range(len(nodes))}
    keys = [node_key(n, f'N{i}') for i, n in enumerate(nodes)]
    blocked = set()
    for edge in blocked_edges or []:
        a, b = str(edge.get('a')), str(edge.get('b'))
        blocked.add(tuple(sorted((a, b))))

    edge_count = 0
    blocked_count = 0
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if tuple(sorted((keys[i], keys[j]))) in blocked:
                blocked_count += 1
                continue
            d = haversine(nodes[i], nodes[j])
            graph[i].append((j, d))
            graph[j].append((i, d))
            edge_count += 1
    return graph, edge_count, blocked_count


def dijkstra(graph: Dict[int, List[Tuple[int, float]]], source: int) -> Tuple[List[float], List[int]]:
    n = len(graph)
    dist = [float('inf')] * n
    parent = [-1] * n
    dist[source] = 0.0
    pq = [(0.0, source)]
    while pq:
        cur_dist, u = heapq.heappop(pq)
        if cur_dist != dist[u]:
            continue
        for v, w in graph[u]:
            nd = cur_dist + w
            if nd < dist[v]:
                dist[v] = nd
                parent[v] = u
                heapq.heappush(pq, (nd, v))
    return dist, parent


def all_pairs_dijkstra(nodes: List[Dict], blocked_edges=None):
    graph, edge_count, blocked_count = build_complete_graph(nodes, blocked_edges)
    matrix = [dijkstra(graph, s)[0] for s in range(len(nodes))]
    return matrix, graph, edge_count, blocked_count


def route_distance(route: List[int], matrix: List[List[float]]) -> float:
    total = 0.0
    for i in range(len(route) - 1):
        d = matrix[route[i]][route[i + 1]]
        if math.isinf(d):
            return float('inf')
        total += d
    return total


def two_opt(route: List[int], matrix: List[List[float]]):
    """2-opt local-search improvement plus an explainable trace."""
    if len(route) <= 4:
        return route[:], []
    best = route[:]
    steps = []
    improved = True
    while improved:
        improved = False
        best_dist = route_distance(best, matrix)
        for i in range(1, len(best) - 2):
            for j in range(i + 1, len(best) - 1):
                if j - i == 1:
                    continue
                candidate = best[:]
                candidate[i:j] = reversed(best[i:j])
                cand_dist = route_distance(candidate, matrix)
                if cand_dist + 1e-9 < best_dist:
                    steps.append({
                        'type': 'tsp',
                        'title': '2-opt improvement',
                        'detail': f'Reversed route section {i}–{j-1}; distance improved by {best_dist-cand_dist:.2f} km.'
                    })
                    best = candidate
                    best_dist = cand_dist
                    improved = True
    return best, steps


def urgency_score(p: Dict) -> float:
    fill = float(p.get('fill_percent', 100))
    priority = float(p.get('priority', 3))
    return fill + priority * 8


def greedy_capacity_trips(pickups: List[Dict], capacity: float, matrix: List[List[float]], improve=True):
    remaining = set(range(1, len(pickups) + 1))
    trips: List[List[int]] = []
    trace = []

    while remaining:
        load = 0.0
        current = 0
        trip = [0]
        progressed = False
        trip_no = len(trips) + 1

        while True:
            feasible = [idx for idx in remaining if load + float(pickups[idx - 1]['waste_kg']) <= capacity + 1e-9]
            feasible = [idx for idx in feasible if not math.isinf(matrix[current][idx])]
            if not feasible:
                break

            def candidate_score(idx: int):
                p = pickups[idx - 1]
                d = matrix[current][idx]
                urgency = urgency_score(p)
                return d / max(1.0, urgency / 50.0)

            nxt = min(feasible, key=candidate_score)
            p = pickups[nxt - 1]
            trace.append({
                'type': 'greedy',
                'title': f'Trip {trip_no}: choose {p["name"]}',
                'detail': f'{matrix[current][nxt]:.2f} km from current node; fill {p.get("fill_percent",100)}%, priority {p.get("priority",3)}, load becomes {load + float(p["waste_kg"]):.0f}/{capacity:.0f} kg.'
            })
            trip.append(nxt)
            load += float(p['waste_kg'])
            remaining.remove(nxt)
            current = nxt
            progressed = True

        if not progressed:
            bad = min(remaining)
            p = pickups[bad - 1]
            if float(p['waste_kg']) > capacity:
                raise ValueError(f"Pickup '{p['name']}' has {p['waste_kg']} kg, which exceeds vehicle capacity of {capacity} kg.")
            raise ValueError(f"Pickup '{p['name']}' became unreachable after the simulated road closures. Remove a closure or add another connecting point.")

        if math.isinf(matrix[current][0]):
            raise ValueError('A simulated closure prevents a selected trip from returning to the dump/depot.')
        trip.append(0)
        if improve:
            trip, opt_steps = two_opt(trip, matrix)
            trace.extend(opt_steps)
        trips.append(trip)

    return trips, trace


def baseline_capacity_trips(pickups: List[Dict], capacity: float, matrix: List[List[float]]) -> List[List[int]]:
    trips, route, load = [], [0], 0.0
    for idx, p in enumerate(pickups, start=1):
        w = float(p['waste_kg'])
        if w > capacity:
            raise ValueError(f"Pickup '{p['name']}' exceeds vehicle capacity.")
        if load + w > capacity and len(route) > 1:
            route.append(0)
            trips.append(route)
            route, load = [0], 0.0
        route.append(idx)
        load += w
    if len(route) > 1:
        route.append(0)
        trips.append(route)
    # A blocked direct link can make a baseline route impossible; Dijkstra matrix will
    # still contain a finite detour when other graph nodes connect the endpoints.
    return trips


def serialize_trip(route: List[int], pickups: List[Dict], matrix: List[List[float]], capacity: float, trip_no: int) -> Dict:
    pickup_indices = [idx for idx in route if idx != 0]
    load = sum(float(pickups[idx - 1]['waste_kg']) for idx in pickup_indices)
    distance = route_distance(route, matrix)
    return {
        'trip_no': trip_no,
        'route_indices': route,
        'route_names': ['Dump' if idx == 0 else pickups[idx - 1]['name'] for idx in route],
        'distance_km': round(distance, 3),
        'load_kg': round(load, 2),
        'capacity_kg': capacity,
        'utilization_percent': round((load / capacity) * 100, 1) if capacity else 0,
        'stops': len(pickup_indices),
    }


def assign_trips_to_vehicles(trips: List[Dict], vehicle_count: int):
    vehicle_count = max(1, int(vehicle_count))
    totals = [0.0] * vehicle_count
    for trip in sorted(trips, key=lambda t: t['distance_km'], reverse=True):
        vehicle = min(range(vehicle_count), key=lambda v: totals[v])
        trip['vehicle_no'] = vehicle + 1
        totals[vehicle] += trip['distance_km']
    return [round(x, 2) for x in totals]


def safe_total(routes: List[List[int]], matrix):
    vals = [route_distance(r, matrix) for r in routes]
    return sum(vals) if all(not math.isinf(x) for x in vals) else float('inf')


def optimize(dump: Dict, pickups: List[Dict], capacity: float, min_fill: float = 0,
             mileage_kmpl: float = 4.0, fuel_price: float = 100.0,
             vehicle_count: int = 1, blocked_edges=None, co2_kg_per_l: float = 2.68) -> Dict:
    if capacity <= 0:
        raise ValueError('Vehicle capacity must be greater than 0.')
    if vehicle_count <= 0:
        raise ValueError('Vehicle count must be at least 1.')

    eligible = [p for p in pickups if float(p.get('fill_percent', 100)) >= min_fill]
    skipped = [p for p in pickups if float(p.get('fill_percent', 100)) < min_fill]
    decision_steps = [{
        'type': 'filter', 'title': 'Fill-level filter',
        'detail': f'{len(eligible)} pickup(s) selected; {len(skipped)} skipped below the {min_fill:.0f}% threshold.'
    }]

    if not eligible:
        return {
            'trips': [], 'baseline_trips': [], 'eligible_pickups': [], 'skipped': skipped,
            'decision_steps': decision_steps, 'comparison': [], 'graph': {'nodes': [], 'edges': []},
            'alerts': ['No bins meet the selected fill threshold.'],
            'summary': {'eligible_pickups': 0, 'skipped_pickups': len(skipped), 'total_waste_kg': 0,
                        'optimized_distance_km': 0, 'baseline_distance_km': 0, 'greedy_distance_km': 0,
                        'distance_saved_km': 0, 'distance_saved_percent': 0, 'estimated_fuel_l': 0,
                        'estimated_fuel_cost': 0, 'estimated_co2_kg': 0, 'trip_count': 0,
                        'vehicle_count': int(vehicle_count), 'fleet_distance_by_vehicle': []}
        }

    nodes = [dump] + eligible
    matrix, graph_adj, edge_count, blocked_count = all_pairs_dijkstra(nodes, blocked_edges)
    decision_steps.append({'type': 'graph', 'title': 'Graph constructed', 'detail': f'{len(nodes)} vertices and {edge_count} active weighted edges; {blocked_count} simulated link(s) blocked.'})
    decision_steps.append({'type': 'dijkstra', 'title': 'Dijkstra shortest paths', 'detail': f'Ran Dijkstra from all {len(nodes)} vertices to build the shortest-cost matrix used by the routing algorithms.'})

    greedy_routes, greedy_trace = greedy_capacity_trips(eligible, capacity, matrix, improve=False)
    optimized_routes, opt_trace = greedy_capacity_trips(eligible, capacity, matrix, improve=True)
    decision_steps.extend(opt_trace)
    baseline_routes = baseline_capacity_trips(eligible, capacity, matrix)

    trips = [serialize_trip(r, eligible, matrix, capacity, i + 1) for i, r in enumerate(optimized_routes)]
    baseline = [serialize_trip(r, eligible, matrix, capacity, i + 1) for i, r in enumerate(baseline_routes)]
    greedy_serialized = [serialize_trip(r, eligible, matrix, capacity, i + 1) for i, r in enumerate(greedy_routes)]
    vehicle_totals = assign_trips_to_vehicles(trips, vehicle_count)

    optimized_distance = sum(t['distance_km'] for t in trips)
    baseline_distance = sum(t['distance_km'] for t in baseline)
    greedy_distance = sum(t['distance_km'] for t in greedy_serialized)
    saved = max(0.0, baseline_distance - optimized_distance)
    saved_pct = (saved / baseline_distance * 100) if baseline_distance > 0 else 0
    fuel = optimized_distance / mileage_kmpl if mileage_kmpl > 0 else 0
    co2 = fuel * max(0.0, co2_kg_per_l)

    alerts = []
    critical = [p for p in eligible if float(p.get('fill_percent', 0)) >= 90 or int(p.get('priority', 0)) >= 5]
    if critical:
        alerts.append(f'{len(critical)} critical/near-overflow bin(s) require priority collection.')
    if skipped:
        alerts.append(f'{len(skipped)} low-fill bin(s) skipped to avoid unnecessary travel.')
    if len(trips) > vehicle_count:
        alerts.append(f'{len(trips)} capacity trips are distributed across {vehicle_count} vehicle(s).')
    if blocked_count:
        alerts.append(f'{blocked_count} simulated graph link(s) are closed; route costs were recalculated.')
    if saved_pct >= 10:
        alerts.append(f'Optimization cuts algorithmic travel by {saved_pct:.1f}% versus first-entered-first-served routing.')

    graph_nodes = [{'index': i, 'uid': node_key(n, f'N{i}'), 'name': 'Dump' if i == 0 else n['name'], 'type': 'dump' if i == 0 else 'pickup'} for i, n in enumerate(nodes)]
    graph_edges = []
    seen = set()
    for i, links in graph_adj.items():
        for j, w in links:
            if (j, i) in seen:
                continue
            seen.add((i, j))
            graph_edges.append({'source': i, 'target': j, 'weight': round(w, 2)})

    comparison = [
        {'name': 'FCFS baseline', 'distance_km': round(baseline_distance, 2), 'description': 'Input order, capacity-aware'},
        {'name': 'Greedy', 'distance_km': round(greedy_distance, 2), 'description': 'Distance + urgency selection'},
        {'name': 'Greedy + 2-opt', 'distance_km': round(optimized_distance, 2), 'description': 'Final TSP-style improvement'},
    ]

    return {
        'trips': trips,
        'baseline_trips': baseline,
        'greedy_trips': greedy_serialized,
        'eligible_pickups': eligible,
        'skipped': skipped,
        'decision_steps': decision_steps,
        'alerts': alerts,
        'comparison': comparison,
        'graph': {'nodes': graph_nodes, 'edges': graph_edges, 'blocked_edges': blocked_edges or []},
        'summary': {
            'eligible_pickups': len(eligible), 'skipped_pickups': len(skipped),
            'total_waste_kg': round(sum(float(p['waste_kg']) for p in eligible), 2),
            'optimized_distance_km': round(optimized_distance, 2), 'baseline_distance_km': round(baseline_distance, 2),
            'greedy_distance_km': round(greedy_distance, 2), 'distance_saved_km': round(saved, 2),
            'distance_saved_percent': round(saved_pct, 1), 'estimated_fuel_l': round(fuel, 2),
            'estimated_fuel_cost': round(fuel * fuel_price, 2), 'estimated_co2_kg': round(co2, 2),
            'trip_count': len(trips), 'vehicle_count': int(vehicle_count),
            'fleet_distance_by_vehicle': vehicle_totals,
        },
        'algorithm': {
            'shortest_path': 'Dijkstra',
            'routing': 'Greedy nearest/urgency selection + 2-opt TSP improvement',
            'capacity': 'Capacity-constrained multi-trip routing with fleet assignment',
            'graph_model': 'Dump and pickup points are vertices; geographic costs are weighted edges',
            'dijkstra_complexity': 'O((V + E) log V) per source with a priority queue',
            'route_problem': 'Capacitated Vehicle Routing / TSP-style optimization',
        }
    }
