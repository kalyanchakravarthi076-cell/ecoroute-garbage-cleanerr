import csv
import io
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone

import folium
import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from streamlit_folium import st_folium

from optimizer import optimize

st.set_page_config(page_title="EcoRoute — Streamlit v2", page_icon="♻️", layout="wide")

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "ecoroute.db")
OSRM_BASE_URL = "https://router.project-osrm.org"
DEFAULT_CENTER = [17.3850, 78.4867]

SAMPLE_DUMP = {"uid": "DUMP", "name": "Municipal Dump", "lat": 17.3850, "lng": 78.4867}
SAMPLE_PICKUPS = [
    {"uid": "P1", "name": "Bin A — Abids", "lat": 17.3933, "lng": 78.4734, "waste_kg": 160, "fill_percent": 96, "priority": 5},
    {"uid": "P2", "name": "Bin B — Koti", "lat": 17.3859, "lng": 78.4869, "waste_kg": 120, "fill_percent": 82, "priority": 4},
    {"uid": "P3", "name": "Bin C — Himayatnagar", "lat": 17.4019, "lng": 78.4846, "waste_kg": 140, "fill_percent": 91, "priority": 5},
    {"uid": "P4", "name": "Bin D — Narayanguda", "lat": 17.3976, "lng": 78.4925, "waste_kg": 110, "fill_percent": 77, "priority": 3},
    {"uid": "P5", "name": "Bin E — Nampally", "lat": 17.3849, "lng": 78.4581, "waste_kg": 170, "fill_percent": 100, "priority": 5},
    {"uid": "P6", "name": "Bin F — Lakdikapul", "lat": 17.4040, "lng": 78.4613, "waste_kg": 100, "fill_percent": 69, "priority": 2},
]

PRESETS = {
    "Normal day": {"capacity": 500.0, "vehicles": 1, "min_fill": 60, "mileage": 4.0, "fuel_price": 100.0, "co2": 2.68},
    "Festival surge": {"capacity": 650.0, "vehicles": 2, "min_fill": 40, "mileage": 3.8, "fuel_price": 100.0, "co2": 2.68},
    "Fuel saver": {"capacity": 650.0, "vehicles": 1, "min_fill": 75, "mileage": 4.5, "fuel_price": 100.0, "co2": 2.68},
    "Critical only": {"capacity": 500.0, "vehicles": 1, "min_fill": 90, "mileage": 4.0, "fuel_price": 100.0, "co2": 2.68},
}

ROUTE_COLORS = ["#00A878", "#4C78A8", "#F58518", "#B279A2", "#E45756", "#72B7B2", "#54A24B", "#EECA3B"]


def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS route_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                scenario TEXT,
                request_json TEXT NOT NULL,
                result_json TEXT NOT NULL
            )"""
        )
        con.commit()


def save_history(payload, result):
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT INTO route_history(created_at,scenario,request_json,result_json) VALUES (?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                payload.get("scenario_name", "Custom"),
                json.dumps(payload),
                json.dumps(result),
            ),
        )
        con.commit()


def load_history(limit=25):
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id,created_at,scenario,request_json,result_json FROM route_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "created_at": r["created_at"],
            "scenario": r["scenario"],
            "request": json.loads(r["request_json"]),
            "result": json.loads(r["result_json"]),
        }
        for r in rows
    ]


def delete_history(history_id):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM route_history WHERE id = ?", (int(history_id),))
        con.commit()


def road_route(points):
    if len(points) < 2:
        return None
    if len(points) > 100:
        return None
    coords = ";".join(f"{float(p['lng']):.7f},{float(p['lat']):.7f}" for p in points)
    query = urllib.parse.urlencode({"overview": "full", "geometries": "geojson", "steps": "false"})
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coords}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "EcoRoute-Streamlit-Hackathon/2.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code") != "Ok" or not payload.get("routes"):
            return None
        route = payload["routes"][0]
        return {
            "route_points": [[lat, lng] for lng, lat in route.get("geometry", {}).get("coordinates", [])],
            "distance_km": float(route.get("distance", 0)) / 1000,
            "duration_min": float(route.get("duration", 0)) / 60,
            "provider": "OSRM / OpenStreetMap",
        }
    except Exception:
        return None


def build_pdf(result, payload, road_routes):
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=A4)
    width, height = A4
    y = height - 50

    def line(text, font="Helvetica", size=9, gap=14):
        nonlocal y
        if y < 65:
            c.showPage()
            y = height - 50
        c.setFont(font, size)
        c.drawString(45, y, str(text)[:115])
        y -= gap

    line("EcoRoute — Smart Garbage Collection Optimizer", "Helvetica-Bold", 17, 24)
    line(f"Scenario: {payload.get('scenario_name', 'Custom')}", "Helvetica", 10, 15)
    line(f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}", "Helvetica", 10, 24)

    s = result.get("summary", {})
    line("Optimization Summary", "Helvetica-Bold", 12, 18)
    summary_rows = [
        ("Optimized distance", f"{s.get('optimized_distance_km', 0)} km"),
        ("FCFS baseline", f"{s.get('baseline_distance_km', 0)} km"),
        ("Distance saved", f"{s.get('distance_saved_percent', 0)}% ({s.get('distance_saved_km', 0)} km)"),
        ("Waste selected", f"{s.get('total_waste_kg', 0)} kg"),
        ("Trips / vehicles", f"{s.get('trip_count', 0)} / {s.get('vehicle_count', 0)}"),
        ("Estimated fuel", f"{s.get('estimated_fuel_l', 0)} L"),
        ("Estimated fuel cost", f"INR {s.get('estimated_fuel_cost', 0)}"),
        ("Estimated CO2", f"{s.get('estimated_co2_kg', 0)} kg"),
        ("Road distance", f"{sum(r.get('distance_km', 0) for r in road_routes if r):.2f} km"),
        ("Road travel time", f"{sum(r.get('duration_min', 0) for r in road_routes if r):.1f} min"),
    ]
    for k, v in summary_rows:
        line(f"{k}: {v}")

    y -= 5
    line("Algorithms", "Helvetica-Bold", 12, 18)
    algo = result.get("algorithm", {})
    line(f"Shortest path: {algo.get('shortest_path', 'Dijkstra')}")
    line(f"Routing: {algo.get('routing', '')}")
    line(f"Capacity model: {algo.get('capacity', '')}")
    line(f"Complexity: {algo.get('dijkstra_complexity', '')}")

    y -= 5
    line("Fleet Trips", "Helvetica-Bold", 12, 18)
    for t in result.get("trips", []):
        line(f"Vehicle {t.get('vehicle_no','-')} / Trip {t['trip_no']} — {t['distance_km']} km — {t['load_kg']} kg ({t['utilization_percent']}%)", "Helvetica-Bold", 9, 14)
        route_text = " -> ".join(t.get("route_names", []))
        for start in range(0, len(route_text), 100):
            line(route_text[start:start + 100], "Helvetica", 8, 12)

    if result.get("alerts"):
        y -= 5
        line("Smart Alerts", "Helvetica-Bold", 12, 18)
        for alert in result["alerts"]:
            line(f"• {alert}")

    c.save()
    bio.seek(0)
    return bio.getvalue()


def trips_csv(result, road_routes):
    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerow(["Vehicle", "Trip", "Route", "Algorithmic distance km", "Road distance km", "Road time min", "Load kg", "Utilization %"])
    for i, t in enumerate(result.get("trips", [])):
        rr = road_routes[i] if i < len(road_routes) else None
        writer.writerow([
            t.get("vehicle_no", ""),
            t.get("trip_no", ""),
            " -> ".join(t.get("route_names", [])),
            t.get("distance_km", ""),
            round(rr.get("distance_km", 0), 3) if rr else "",
            round(rr.get("duration_min", 0), 1) if rr else "",
            t.get("load_kg", ""),
            t.get("utilization_percent", ""),
        ])
    return sio.getvalue().encode("utf-8")


def pickups_csv(pickups):
    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerow(["UID", "Name", "Latitude", "Longitude", "Waste kg", "Fill %", "Priority"])
    for p in pickups:
        writer.writerow([p.get("uid"), p.get("name"), p.get("lat"), p.get("lng"), p.get("waste_kg"), p.get("fill_percent"), p.get("priority")])
    return sio.getvalue().encode("utf-8")


def reset_state():
    st.session_state.dump = None
    st.session_state.pickups = []
    st.session_state.blocked_edges = []
    st.session_state.result = None
    st.session_state.road_routes = []
    st.session_state.mode = "pickup"
    st.session_state.pending_click = None
    st.session_state.last_click_sig = None
    st.session_state.last_payload = None
    st.session_state.capacity = 500.0
    st.session_state.vehicles = 1
    st.session_state.min_fill = 60
    st.session_state.mileage = 4.0
    st.session_state.fuel_price = 100.0
    st.session_state.co2 = 2.68
    st.session_state.scenario_name = "Hackathon Demo"


def make_payload():
    return {
        "scenario_name": st.session_state.scenario_name,
        "dump": deepcopy(st.session_state.dump),
        "pickups": deepcopy(st.session_state.pickups),
        "vehicle_capacity": float(st.session_state.capacity),
        "vehicle_count": int(st.session_state.vehicles),
        "min_fill": float(st.session_state.min_fill),
        "mileage_kmpl": float(st.session_state.mileage),
        "fuel_price": float(st.session_state.fuel_price),
        "co2_kg_per_l": float(st.session_state.co2),
        "blocked_edges": deepcopy(st.session_state.blocked_edges),
    }


def perform_optimization(save=True):
    if not st.session_state.dump:
        return False, "Add a dump/depot first."
    if not st.session_state.pickups:
        return False, "Add at least one garbage pickup point."
    payload = make_payload()
    try:
        result = optimize(
            dump=payload["dump"],
            pickups=payload["pickups"],
            capacity=payload["vehicle_capacity"],
            min_fill=payload["min_fill"],
            mileage_kmpl=payload["mileage_kmpl"],
            fuel_price=payload["fuel_price"],
            vehicle_count=payload["vehicle_count"],
            blocked_edges=payload["blocked_edges"],
            co2_kg_per_l=payload["co2_kg_per_l"],
        )
    except (ValueError, TypeError, KeyError) as exc:
        return False, str(exc)

    roads = []
    eligible = result.get("eligible_pickups", [])
    nodes = [st.session_state.dump] + eligible
    for trip in result.get("trips", []):
        points = [nodes[idx] for idx in trip.get("route_indices", [])]
        rr = road_route(points)
        if rr:
            rr["trip_no"] = trip.get("trip_no")
            rr["vehicle_no"] = trip.get("vehicle_no")
        roads.append(rr)

    st.session_state.result = result
    st.session_state.road_routes = roads
    st.session_state.last_payload = payload
    if save:
        save_history(payload, result)
    return True, "Optimization complete."


def apply_preset(name):
    p = PRESETS[name]
    st.session_state.capacity = p["capacity"]
    st.session_state.vehicles = p["vehicles"]
    st.session_state.min_fill = p["min_fill"]
    st.session_state.mileage = p["mileage"]
    st.session_state.fuel_price = p["fuel_price"]
    st.session_state.co2 = p["co2"]
    st.session_state.scenario_name = name


def restore_history(item):
    req = item["request"]
    st.session_state.dump = req.get("dump")
    st.session_state.pickups = req.get("pickups", [])
    st.session_state.blocked_edges = req.get("blocked_edges", [])
    st.session_state.capacity = float(req.get("vehicle_capacity", 500))
    st.session_state.vehicles = int(req.get("vehicle_count", 1))
    st.session_state.min_fill = int(req.get("min_fill", 60))
    st.session_state.mileage = float(req.get("mileage_kmpl", 4.0))
    st.session_state.fuel_price = float(req.get("fuel_price", 100.0))
    st.session_state.co2 = float(req.get("co2_kg_per_l", 2.68))
    st.session_state.scenario_name = req.get("scenario_name", item.get("scenario", "Restored"))
    st.session_state.result = item.get("result")
    st.session_state.road_routes = []
    st.session_state.last_payload = req


def restore_history_and_optimize(item):
    """Restore a saved scenario in a Streamlit callback, then recompute its route."""
    restore_history(item)
    perform_optimization(save=False)


def pickup_status(fill, priority):
    if float(fill) >= 90 or int(priority) >= 5:
        return "Critical"
    if float(fill) >= 70 or int(priority) >= 4:
        return "High"
    if float(fill) >= 50:
        return "Normal"
    return "Low"


init_db()
if "dump" not in st.session_state:
    reset_state()

st.title("♻️ EcoRoute — Smart Garbage Collection Optimizer")
st.caption("Streamlit-only DAA prototype · Dijkstra + Greedy + TSP/2-opt + capacity-aware fleet routing + real-road visualization")

with st.sidebar:
    st.header("Control Center")
    st.text_input("Scenario name", key="scenario_name")
    st.number_input("Vehicle capacity (kg)", min_value=50.0, step=50.0, key="capacity")
    st.number_input("Number of vehicles", min_value=1, max_value=10, step=1, key="vehicles")
    st.slider("Minimum fill level to collect", 0, 100, key="min_fill")
    st.number_input("Mileage (km/L)", min_value=0.1, step=0.5, key="mileage")
    st.number_input("Fuel price (₹/L)", min_value=0.0, step=5.0, key="fuel_price")
    st.number_input("CO₂ factor (kg/L)", min_value=0.0, step=0.1, key="co2")

    st.divider()
    st.subheader("Scenario simulator")
    p1, p2 = st.columns(2)
    p1.button("Normal", use_container_width=True, on_click=apply_preset, args=("Normal day",))
    p2.button("Festival", use_container_width=True, on_click=apply_preset, args=("Festival surge",))
    p3, p4 = st.columns(2)
    p3.button("Fuel saver", use_container_width=True, on_click=apply_preset, args=("Fuel saver",))
    p4.button("Critical", use_container_width=True, on_click=apply_preset, args=("Critical only",))

    st.divider()
    st.subheader("Scenario data")
    a, b = st.columns(2)
    if a.button("Load sample", use_container_width=True):
        st.session_state.dump = deepcopy(SAMPLE_DUMP)
        st.session_state.pickups = deepcopy(SAMPLE_PICKUPS)
        st.session_state.blocked_edges = []
        st.session_state.result = None
        st.session_state.road_routes = []
        st.rerun()
    b.button("Reset", use_container_width=True, on_click=reset_state)

    st.subheader("Map click mode")
    mode = st.radio(
        "Map click action",
        ["Add pickup", "Add / change dump"],
        index=0 if st.session_state.mode == "pickup" else 1,
        key="map_click_action",
    )
    new_mode = "dump" if mode.startswith("Add / change") else "pickup"
    if new_mode != st.session_state.mode:
        # Clear only the pending confirmation when switching tools.  Do NOT
        # clear last_click_sig: streamlit-folium keeps the previous click
        # across reruns, and clearing it would make that stale click look new.
        st.session_state.mode = new_mode
        st.session_state.pending_click = None

main_map, control_panel = st.columns([1.55, 1], gap="large")

with control_panel:
    st.subheader("Depot & collection points")
    if st.session_state.dump:
        st.success(f"🏭 {st.session_state.dump.get('name','Dump / Depot')} · {st.session_state.dump['lat']:.5f}, {st.session_state.dump['lng']:.5f}")
    else:
        st.warning("Select **Add / change dump** in the sidebar and click the map.")

    with st.expander("➕ Add pickup manually", expanded=False):
        with st.form("manual_add"):
            name = st.text_input("Pickup name", value=f"Pickup {len(st.session_state.pickups) + 1}")
            c1, c2 = st.columns(2)
            lat = c1.number_input("Latitude", value=17.3850, format="%.6f")
            lng = c2.number_input("Longitude", value=78.4867, format="%.6f")
            c3, c4, c5 = st.columns(3)
            waste = c3.number_input("Waste kg", min_value=1.0, value=100.0)
            fill = c4.slider("Fill %", 0, 100, 80)
            priority = c5.selectbox("Priority", [1, 2, 3, 4, 5], index=2)
            if st.form_submit_button("Add pickup", use_container_width=True):
                st.session_state.pickups.append({
                    "uid": f"P{int(time.time() * 1000)}",
                    "name": name.strip() or f"Pickup {len(st.session_state.pickups) + 1}",
                    "lat": float(lat),
                    "lng": float(lng),
                    "waste_kg": float(waste),
                    "fill_percent": int(fill),
                    "priority": int(priority),
                })
                st.session_state.result = None
                st.session_state.road_routes = []
                st.rerun()

    if st.session_state.pickups:
        st.markdown("**Edit pickup points**")
        editable = pd.DataFrame(st.session_state.pickups)[["uid", "name", "lat", "lng", "waste_kg", "fill_percent", "priority"]]
        edited = st.data_editor(
            editable,
            use_container_width=True,
            hide_index=True,
            disabled=["uid"],
            column_config={
                "uid": st.column_config.TextColumn("ID"),
                "name": st.column_config.TextColumn("Name", required=True),
                "lat": st.column_config.NumberColumn("Latitude", format="%.6f"),
                "lng": st.column_config.NumberColumn("Longitude", format="%.6f"),
                "waste_kg": st.column_config.NumberColumn("Waste kg", min_value=1.0),
                "fill_percent": st.column_config.NumberColumn("Fill %", min_value=0, max_value=100),
                "priority": st.column_config.NumberColumn("Priority", min_value=1, max_value=5),
            },
            key="pickup_editor",
        )
        e1, e2 = st.columns(2)
        if e1.button("💾 Save edits", use_container_width=True):
            cleaned = []
            for row in edited.to_dict("records"):
                cleaned.append({
                    "uid": str(row["uid"]),
                    "name": str(row["name"]).strip(),
                    "lat": float(row["lat"]),
                    "lng": float(row["lng"]),
                    "waste_kg": max(1.0, float(row["waste_kg"])),
                    "fill_percent": min(100, max(0, int(row["fill_percent"]))),
                    "priority": min(5, max(1, int(row["priority"]))),
                })
            st.session_state.pickups = cleaned
            st.session_state.result = None
            st.session_state.road_routes = []
            st.success("Pickup edits saved.")
            st.rerun()
        remove_uid = e2.selectbox("Remove", ["—"] + [p["uid"] for p in st.session_state.pickups], format_func=lambda uid: "Choose pickup" if uid == "—" else next((p["name"] for p in st.session_state.pickups if p["uid"] == uid), uid), label_visibility="collapsed")
        if remove_uid != "—" and st.button("🗑️ Remove selected pickup", use_container_width=True):
            st.session_state.pickups = [p for p in st.session_state.pickups if p["uid"] != remove_uid]
            st.session_state.blocked_edges = [e for e in st.session_state.blocked_edges if remove_uid not in (e.get("a"), e.get("b"))]
            st.session_state.result = None
            st.session_state.road_routes = []
            st.rerun()
    else:
        st.info("No pickup points added yet.")

    st.subheader("⛔ Road / graph closure simulation")
    node_options = (["DUMP"] if st.session_state.dump else []) + [p["uid"] for p in st.session_state.pickups]
    labels = {"DUMP": "Dump / Depot"}
    labels.update({p["uid"]: p["name"] for p in st.session_state.pickups})
    if len(node_options) >= 2:
        ca, cb = st.columns(2)
        a = ca.selectbox("From", node_options, format_func=lambda x: labels.get(x, x), key="closure_a")
        b_choices = [x for x in node_options if x != a]
        b = cb.selectbox("To", b_choices, format_func=lambda x: labels.get(x, x), key="closure_b")
        if st.button("Block link & recalculate", use_container_width=True):
            norm = {tuple(sorted((e["a"], e["b"]))) for e in st.session_state.blocked_edges}
            edge_key = tuple(sorted((a, b)))
            if edge_key not in norm:
                st.session_state.blocked_edges.append({"a": a, "b": b})
            ok, message = perform_optimization(save=True)
            if not ok:
                st.error(message)
            st.rerun()
    if st.session_state.blocked_edges:
        for idx, edge in enumerate(st.session_state.blocked_edges):
            c1, c2 = st.columns([5, 1])
            c1.caption(f"⛔ {labels.get(edge['a'], edge['a'])} ↔ {labels.get(edge['b'], edge['b'])}")
            if c2.button("✕", key=f"remove_closure_{idx}"):
                st.session_state.blocked_edges.pop(idx)
                ok, _ = perform_optimization(save=True) if st.session_state.dump and st.session_state.pickups else (False, "")
                st.rerun()
        if st.button("Clear all closures & recalculate", use_container_width=True):
            st.session_state.blocked_edges = []
            ok, _ = perform_optimization(save=True) if st.session_state.dump and st.session_state.pickups else (False, "")
            st.rerun()

with main_map:
    st.subheader("Live Route Map")
    center = DEFAULT_CENTER
    if st.session_state.dump:
        center = [st.session_state.dump["lat"], st.session_state.dump["lng"]]
    elif st.session_state.pickups:
        center = [st.session_state.pickups[0]["lat"], st.session_state.pickups[0]["lng"]]

    m = folium.Map(location=center, zoom_start=13, tiles="OpenStreetMap", control_scale=True)

    if st.session_state.dump:
        d = st.session_state.dump
        folium.Marker(
            [d["lat"], d["lng"]],
            tooltip="Dump / Depot",
            popup=f"{d.get('name','Dump / Depot')}",
            icon=folium.Icon(color="green", icon="industry", prefix="fa"),
        ).add_to(m)

    result = st.session_state.get("result")
    eligible_uids = {p.get("uid") for p in result.get("eligible_pickups", [])} if result else set()
    skipped_uids = {p.get("uid") for p in result.get("skipped", [])} if result else set()

    for p in st.session_state.pickups:
        status = pickup_status(p.get("fill_percent", 0), p.get("priority", 1))
        if p.get("uid") in skipped_uids:
            color = "lightgray"
        elif status == "Critical":
            color = "red"
        elif status == "High":
            color = "orange"
        else:
            color = "blue"
        folium.Marker(
            [p["lat"], p["lng"]],
            tooltip=f"{p['name']} · {status}",
            popup=f"{p['name']} | {p['fill_percent']}% full | {p['waste_kg']} kg | Priority {p['priority']}",
            icon=folium.Icon(color=color, icon="trash", prefix="fa"),
        ).add_to(m)

    lookup = {"DUMP": st.session_state.dump} if st.session_state.dump else {}
    lookup.update({p["uid"]: p for p in st.session_state.pickups})
    for edge in st.session_state.blocked_edges:
        pa, pb = lookup.get(edge["a"]), lookup.get(edge["b"])
        if pa and pb:
            folium.PolyLine(
                [[pa["lat"], pa["lng"]], [pb["lat"], pb["lng"]]],
                color="red",
                weight=4,
                dash_array="8 8",
                tooltip="Simulated closed graph link",
            ).add_to(m)

    for idx, rr in enumerate(st.session_state.get("road_routes", [])):
        if rr and rr.get("route_points"):
            vehicle_no = int(rr.get("vehicle_no") or 1)
            color = ROUTE_COLORS[(vehicle_no - 1) % len(ROUTE_COLORS)]
            folium.PolyLine(
                rr["route_points"],
                weight=6,
                opacity=0.86,
                color=color,
                tooltip=f"Vehicle {vehicle_no} · Trip {rr.get('trip_no', idx + 1)} · real road route",
            ).add_to(m)

    # Make map placement explicit: once a new pickup location is clicked,
    # show a temporary marker exactly at that coordinate until confirmed.
    if st.session_state.pending_click and st.session_state.mode == "pickup":
        pc = st.session_state.pending_click
        folium.Marker(
            [pc["lat"], pc["lng"]],
            tooltip="Selected pickup position — confirm below",
            popup=f"Exact selected point: {pc['lat']:.6f}, {pc['lng']:.6f}",
            icon=folium.Icon(color="purple", icon="plus", prefix="fa"),
        ).add_to(m)

    available_roads = [(i, rr) for i, rr in enumerate(st.session_state.get("road_routes", [])) if rr and rr.get("route_points")]
    if available_roads:
        road_choice = st.selectbox(
            "🚛 Truck visualization",
            [i for i, _ in available_roads],
            format_func=lambda i: f"Vehicle {st.session_state.road_routes[i].get('vehicle_no','-')} · Trip {st.session_state.road_routes[i].get('trip_no', i + 1)}",
            key="truck_trip_choice",
        )
        progress = st.slider("Truck progress", 0, 100, 0, key="truck_progress")
        rr = st.session_state.road_routes[road_choice]
        pts = rr["route_points"]
        pos = min(len(pts) - 1, int((progress / 100) * (len(pts) - 1)))
        folium.Marker(
            pts[pos],
            tooltip=f"Vehicle {rr.get('vehicle_no','-')} · {progress}% complete",
            icon=folium.Icon(color="blue", icon="truck", prefix="fa"),
        ).add_to(m)

    map_out = st_folium(
        m,
        height=660,
        use_container_width=True,
        key="main_map_v2",
        returned_objects=["last_clicked"],
    )
    click = map_out.get("last_clicked") if map_out else None
    if click and click.get("lat") is not None and click.get("lng") is not None:
        # streamlit-folium persists last_clicked between reruns. Compare only
        # coordinates (not the current tool/mode), otherwise switching from
        # Dump -> Pickup can accidentally reuse an old click.
        sig = (round(float(click["lat"]), 7), round(float(click["lng"]), 7))
        if st.session_state.last_click_sig != sig:
            st.session_state.last_click_sig = sig
            exact_click = {"lat": float(click["lat"]), "lng": float(click["lng"])}
            if st.session_state.mode == "dump":
                st.session_state.dump = {
                    "uid": "DUMP",
                    "name": "Dump / Depot",
                    "lat": exact_click["lat"],
                    "lng": exact_click["lng"],
                }
                st.session_state.pending_click = None
                st.session_state.result = None
                st.session_state.road_routes = []
                st.rerun()
            else:
                st.session_state.pending_click = exact_click
                # Rerun immediately so the temporary marker appears exactly
                # where the user clicked before they confirm the pickup.
                st.rerun()

    if st.session_state.pending_click and st.session_state.mode == "pickup":
        pc = st.session_state.pending_click
        st.success(f"📍 Exact pickup position selected: {pc['lat']:.7f}, {pc['lng']:.7f}")
        st.caption("The purple marker on the map is the exact point that will be saved. Click somewhere else on the map to change it before confirming.")
        with st.form("map_pickup_form"):
            mp_name = st.text_input("Pickup name", f"Pickup {len(st.session_state.pickups) + 1}")
            c1, c2, c3 = st.columns(3)
            mp_waste = c1.number_input("Waste kg", min_value=1.0, value=100.0)
            mp_fill = c2.slider("Fill %", 0, 100, 80)
            mp_priority = c3.selectbox("Priority", [1, 2, 3, 4, 5], index=2)
            confirm_col, cancel_col = st.columns(2)
            confirm = confirm_col.form_submit_button("✅ Confirm exact pickup", use_container_width=True)
            cancel = cancel_col.form_submit_button("Cancel", use_container_width=True)
            if confirm:
                st.session_state.pickups.append({
                    "uid": f"P{int(time.time() * 1000)}",
                    "name": mp_name.strip() or f"Pickup {len(st.session_state.pickups) + 1}",
                    "lat": float(pc["lat"]),
                    "lng": float(pc["lng"]),
                    "waste_kg": float(mp_waste),
                    "fill_percent": int(mp_fill),
                    "priority": int(mp_priority),
                })
                st.session_state.pending_click = None
                st.session_state.result = None
                st.session_state.road_routes = []
                st.rerun()
            if cancel:
                st.session_state.pending_click = None
                st.rerun()

    st.caption("Map legend: green depot · red critical pickup · orange high priority · blue normal pickup · gray skipped pickup · red dashed simulated closure · vehicle routes use different colors.")

st.divider()
run_col, status_col = st.columns([1, 3])
with run_col:
    if st.button("⚡ Optimize / Recalculate", type="primary", use_container_width=True):
        ok, message = perform_optimization(save=True)
        if ok:
            st.success(message)
            st.rerun()
        else:
            st.error(message)
with status_col:
    if st.session_state.result:
        road_ok = sum(1 for r in st.session_state.road_routes if r)
        st.info(f"Optimizer ready · {len(st.session_state.result.get('trips', []))} algorithmic trip(s) · {road_ok}/{len(st.session_state.road_routes)} real-road route(s) loaded")
    else:
        st.info("Add a depot and pickups, then run the optimizer.")

result = st.session_state.get("result")
if result:
    st.header("Optimization Results")
    s = result.get("summary", {})
    roads = st.session_state.get("road_routes", [])
    road_distance = sum(r.get("distance_km", 0) for r in roads if r)
    road_time = sum(r.get("duration_min", 0) for r in roads if r)

    r1 = st.columns(6)
    r1[0].metric("Optimized", f"{s.get('optimized_distance_km', 0)} km")
    r1[1].metric("Baseline", f"{s.get('baseline_distance_km', 0)} km")
    r1[2].metric("Saved", f"{s.get('distance_saved_percent', 0)}%", f"{s.get('distance_saved_km', 0)} km")
    r1[3].metric("Trips / vehicles", f"{s.get('trip_count', 0)} / {s.get('vehicle_count', 0)}")
    r1[4].metric("Fuel", f"{s.get('estimated_fuel_l', 0)} L", f"₹{s.get('estimated_fuel_cost', 0)}")
    r1[5].metric("CO₂", f"{s.get('estimated_co2_kg', 0)} kg")

    r2 = st.columns(4)
    r2[0].metric("Real-road distance", f"{road_distance:.2f} km")
    r2[1].metric("Road ETA", f"{road_time:.1f} min")
    r2[2].metric("Waste selected", f"{s.get('total_waste_kg', 0)} kg")
    r2[3].metric("Skipped bins", s.get("skipped_pickups", 0))
    st.caption("Dijkstra/Greedy/2-opt choose the stop order. OSRM/OpenStreetMap only project that order onto actual roads for navigation visualization.")

    if result.get("alerts"):
        st.subheader("🔥 Smart Alerts")
        for alert in result["alerts"]:
            if "critical" in alert.lower() or "closed" in alert.lower():
                st.warning(alert)
            elif "cuts" in alert.lower():
                st.success(alert)
            else:
                st.info(alert)

    tabs = st.tabs([
        "🚛 Fleet trips",
        "⚖️ Algorithm comparison",
        "🧠 Algorithm steps",
        "🕸️ Graph",
        "📊 Analytics",
        "🧪 Scenario comparison",
        "📄 Reports",
    ])

    with tabs[0]:
        if result.get("trips"):
            for idx, trip in enumerate(result["trips"]):
                rr = roads[idx] if idx < len(roads) else None
                title = f"Vehicle {trip.get('vehicle_no','-')} · Trip {trip['trip_no']}"
                with st.expander(title, expanded=idx == 0):
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Algorithmic distance", f"{trip['distance_km']} km")
                    c2.metric("Load", f"{trip['load_kg']} / {trip['capacity_kg']} kg")
                    c3.metric("Utilization", f"{trip['utilization_percent']}%")
                    c4.metric("Stops", trip.get("stops", 0))
                    st.write(" → ".join(trip.get("route_names", [])))
                    if rr:
                        st.caption(f"Real-road navigation: {rr['distance_km']:.2f} km · {rr['duration_min']:.1f} min")
                    else:
                        st.caption("Real-road geometry unavailable; algorithmic route remains valid.")
        else:
            st.info("No bins meet the current fill threshold.")

    with tabs[1]:
        comp = pd.DataFrame(result.get("comparison", []))
        if not comp.empty:
            st.dataframe(comp, hide_index=True, use_container_width=True)
            st.bar_chart(comp.set_index("name")["distance_km"], use_container_width=True)
            best = comp.loc[comp["distance_km"].idxmin()]
            st.success(f"Best measured algorithmic route: {best['name']} at {best['distance_km']} km")
        st.markdown("**Comparison logic**")
        st.write("FCFS follows the input order. Greedy selects a locally efficient next pickup using distance and urgency. 2-opt then checks route segment reversals and keeps improvements, producing the final TSP-style route.")

    with tabs[2]:
        steps = result.get("decision_steps", [])
        if steps:
            step_no = st.slider("Inspect algorithm step", 1, len(steps), 1, key="algorithm_step_slider")
            step = steps[step_no - 1]
            st.info(f"Step {step_no}/{len(steps)} · {step.get('title')} — {step.get('detail')}")
            with st.expander("Complete decision trace"):
                for i, item in enumerate(steps, 1):
                    st.write(f"**{i}. {item.get('title')}** — {item.get('detail')}")
            if st.button("▶ Auto-play algorithm trace"):
                holder = st.empty()
                progress = st.progress(0)
                for i, item in enumerate(steps, 1):
                    holder.info(f"Step {i}/{len(steps)} · **{item.get('title')}** — {item.get('detail')}")
                    progress.progress(int(i / len(steps) * 100))
                    time.sleep(0.55)
                holder.success("Algorithm trace complete.")

    with tabs[3]:
        st.markdown("**Graph model:** dump and selected bins are vertices; weighted edges represent travel cost. A simulated road closure removes a graph edge before Dijkstra recomputes shortest paths.")
        graph = result.get("graph", {})
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        dot = ["graph G {", 'rankdir="LR";', 'node [shape=circle];']
        for node in nodes:
            label = str(node.get("name", "Node")).replace('"', "'")
            shape = "doublecircle" if node.get("type") == "dump" else "circle"
            dot.append(f'{node["index"]} [label="{label}", shape={shape}];')
        for edge in edges:
            dot.append(f'{edge["source"]} -- {edge["target"]} [label="{edge["weight"]} km"];')
        dot.append("}")
        if nodes:
            st.graphviz_chart("\n".join(dot), use_container_width=True)
        algo = result.get("algorithm", {})
        st.code(
            f"Dijkstra: {algo.get('dijkstra_complexity','')}\n"
            f"Routing: {algo.get('routing','')}\n"
            f"Capacity: {algo.get('capacity','')}",
            language="text",
        )

    with tabs[4]:
        st.subheader("Fleet & waste analytics")
        a1, a2 = st.columns(2)
        with a1:
            fleet = s.get("fleet_distance_by_vehicle", [])
            if fleet:
                fleet_df = pd.DataFrame({"Vehicle": [f"Vehicle {i + 1}" for i in range(len(fleet))], "Distance km": fleet})
                st.markdown("**Distance by vehicle**")
                st.bar_chart(fleet_df.set_index("Vehicle"), use_container_width=True)
        with a2:
            selected = s.get("eligible_pickups", 0)
            skipped = s.get("skipped_pickups", 0)
            status_df = pd.DataFrame({"Category": ["Selected", "Skipped"], "Bins": [selected, skipped]})
            st.markdown("**Collection selection**")
            st.bar_chart(status_df.set_index("Category"), use_container_width=True)

        if st.session_state.pickups:
            analytics = pd.DataFrame(st.session_state.pickups)
            analytics["status"] = analytics.apply(lambda r: pickup_status(r["fill_percent"], r["priority"]), axis=1)
            st.markdown("**Bin fill levels**")
            fill_df = analytics[["name", "fill_percent"]].set_index("name")
            st.bar_chart(fill_df, use_container_width=True)
            st.dataframe(analytics[["name", "waste_kg", "fill_percent", "priority", "status"]], hide_index=True, use_container_width=True)

    with tabs[5]:
        st.write("Compare how the same pickup network behaves under four operating policies. The pickup data stays the same; fleet/threshold settings change.")
        scenario_rows = []
        for name, preset in PRESETS.items():
            try:
                scenario_result = optimize(
                    dump=st.session_state.dump,
                    pickups=st.session_state.pickups,
                    capacity=preset["capacity"],
                    min_fill=preset["min_fill"],
                    mileage_kmpl=preset["mileage"],
                    fuel_price=preset["fuel_price"],
                    vehicle_count=preset["vehicles"],
                    blocked_edges=st.session_state.blocked_edges,
                    co2_kg_per_l=preset["co2"],
                )
                ss = scenario_result.get("summary", {})
                scenario_rows.append({
                    "Scenario": name,
                    "Min fill %": preset["min_fill"],
                    "Vehicles": preset["vehicles"],
                    "Capacity kg": preset["capacity"],
                    "Selected bins": ss.get("eligible_pickups", 0),
                    "Trips": ss.get("trip_count", 0),
                    "Distance km": ss.get("optimized_distance_km", 0),
                    "Fuel L": ss.get("estimated_fuel_l", 0),
                    "CO₂ kg": ss.get("estimated_co2_kg", 0),
                })
            except Exception as exc:
                scenario_rows.append({"Scenario": name, "Error": str(exc)})
        scenario_df = pd.DataFrame(scenario_rows)
        st.dataframe(scenario_df, hide_index=True, use_container_width=True)
        if "Distance km" in scenario_df.columns:
            valid = scenario_df.dropna(subset=["Distance km"])
            if not valid.empty:
                st.bar_chart(valid.set_index("Scenario")["Distance km"], use_container_width=True)

    with tabs[6]:
        st.subheader("Export")
        payload = st.session_state.last_payload or make_payload()
        c1, c2, c3 = st.columns(3)
        c1.download_button("⬇ Route CSV", trips_csv(result, roads), "ecoroute_routes.csv", "text/csv", use_container_width=True)
        c2.download_button("⬇ Pickup CSV", pickups_csv(st.session_state.pickups), "ecoroute_pickups.csv", "text/csv", use_container_width=True)
        c3.download_button("⬇ PDF report", build_pdf(result, payload, roads), "ecoroute_report.pdf", "application/pdf", use_container_width=True)
        st.markdown("**Report includes:** route summary, algorithms, fleet trips, fuel/CO₂ estimates, real-road totals and smart alerts.")

st.divider()
st.header("📚 Route History — SQLite")
history = load_history()
if not history:
    st.info("No saved routes yet. Every successful optimization is stored automatically.")
else:
    hrows = []
    for h in history:
        sm = h["result"].get("summary", {})
        hrows.append({
            "ID": h["id"],
            "Created": h["created_at"],
            "Scenario": h["scenario"],
            "Optimized km": sm.get("optimized_distance_km"),
            "Saved %": sm.get("distance_saved_percent"),
            "Trips": sm.get("trip_count"),
        })
    st.dataframe(pd.DataFrame(hrows), hide_index=True, use_container_width=True)
    selected_id = st.selectbox("Select saved run", [h["id"] for h in history], format_func=lambda hid: next((f"#{h['id']} · {h['scenario']} · {h['created_at']}" for h in history if h["id"] == hid), str(hid)))
    selected = next(h for h in history if h["id"] == selected_id)
    h1, h2 = st.columns(2)
    h1.button("↩ Restore selected scenario", use_container_width=True, on_click=restore_history_and_optimize, args=(selected,))
    if h2.button("🗑 Delete selected history entry", use_container_width=True):
        delete_history(selected_id)
        st.rerun()

st.caption("EcoRoute Streamlit v2 · No Flask and no custom HTML/CSS/JavaScript files · Internet required only for OpenStreetMap tiles and OSRM road geometry.")
