# EcoRoute

EcoRoute is a Streamlit-only Garbage Collection Route Optimizer developed for a Design and Analysis of Algorithms hackathon.

The system optimizes municipal waste collection by selecting important pickup points, generating efficient collection routes, handling vehicle-capacity constraints, and visualizing the optimized route on real roads.

## Main Algorithms

- Dijkstra shortest-path algorithm
- Greedy priority-based route selection
- TSP-style 2-opt local improvement
- Capacity-constrained multi-trip routing
- Multi-vehicle trip assignment

## Main Features

- Interactive OpenStreetMap interface
- Add or change dump/depot location by clicking the map
- Add garbage pickup points using map clicks or manual coordinates
- Edit pickup name, coordinates, waste quantity, fill level and priority
- Fill-level filtering and skipped-bin visualization
- Multiple vehicles
- Vehicle-capacity constraints
- Vehicle-wise route visualization
- Real-road routing through OSRM
- Truck progress visualization for selected trips
- Graph-edge road closure simulation with route recalculation
- FCFS vs Greedy vs Greedy + 2-opt comparison
- Step-by-step algorithm decision trace
- Algorithm auto-play visualization
- Graph representation of the routing problem
- Smart alerts
- Scenario presets
- Scenario comparison table and charts
- Fleet, waste and fill-level analytics
- SQLite route history
- Restore or delete saved route history
- CSV route report
- CSV pickup report
- PDF report
- Fuel-consumption estimation
- CO₂ emission estimation
- Real-road distance and ETA metrics

## Architecture

EcoRoute separates algorithmic optimization from road visualization.

The DAA algorithms determine:

- which collection points should be visited
- the order in which they should be visited
- how vehicle capacity affects the number of trips
- how multiple vehicles are assigned routes

OSRM and OpenStreetMap are used only to project the already-optimized route onto real drivable roads for visualization.

Therefore, the route optimization itself is performed by the project's DAA algorithms rather than by the external routing service.

## Technology Stack

- Python
- Streamlit
- Folium
- streamlit-folium
- SQLite
- Pandas
- ReportLab
- OpenStreetMap
- OSRM

## Run on Windows

Open Command Prompt inside the project folder and run:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Streamlit normally opens at:

`http://localhost:8501`

## Internet Requirement

Internet access is required for:

- OpenStreetMap map tiles
- OSRM real-road route geometry

The core DAA optimization algorithms run locally.

## Project Structure

The project intentionally uses:

- Streamlit for the user interface
- Python for algorithm implementation and backend logic
- Folium for interactive map rendering

It does not use Flask or separate custom HTML, CSS or JavaScript frontend files.