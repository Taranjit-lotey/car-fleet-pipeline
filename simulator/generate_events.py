import argparse
import json
import os
import random
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


REGIONS = ["US-CA", "US-TX", "US-FL", "US-NY", "US-WA", "EU-DE", "EU-FR", "EU-NO"]
FSD_VERSIONS = ["12.1.0", "12.2.0", "12.3.1", "12.4.0"]
HARDWARE_VERSIONS = ["HW3", "HW4"]
SCENARIO_TYPES = ["highway", "intersection", "residential", "construction_zone", "parking_lot"]
ROAD_TYPES = ["highway", "arterial", "residential"]
WEATHER_CONDITIONS = ["clear", "rain", "fog", "snow", "overcast"]
TIMES_OF_DAY = ["day", "night", "dusk", "dawn"]
TRAFFIC_DENSITIES = ["low", "medium", "high"]

NN_ACTIONS = ["accelerate", "brake", "lane_change_left", "lane_change_right", "turn_left", "turn_right", "stop", "maintain"]

ROAD_SPEED_CAPS = {
    "highway": (45, 85),
    "arterial": (20, 55),
    "residential": (5, 30),
}

WEATHER_VISIBILITY = {
    "clear": (800, 1600),
    "overcast": (600, 1200),
    "rain": (200, 600),
    "fog": (30, 200),
    "snow": (50, 300),
}

# coords roughly bounding the regions we simulate
REGION_COORDS = {
    "US-CA": (32.5, 42.0, -124.4, -114.1),
    "US-TX": (25.8, 36.5, -106.6, -93.5),
    "US-FL": (24.5, 31.0, -87.6, -80.0),
    "US-NY": (40.5, 45.0, -79.8, -71.8),
    "US-WA": (45.5, 49.0, -124.7, -116.9),
    "EU-DE": (47.3, 55.0, 6.0, 15.0),
    "EU-FR": (42.3, 51.1, -4.8, 8.2),
    "EU-NO": (57.9, 71.2, 4.5, 31.1),
}


@dataclass
class DrivingEvent:
    vehicle_id: str
    timestamp: str
    fsd_version: str
    hardware_version: str
    region: str
    latitude: float
    longitude: float
    speed_mph: float
    heading_degrees: int
    acceleration_mps2: float
    brake_pressure_pct: float
    throttle_pct: float
    steering_angle_deg: float
    battery_level_pct: float
    ambient_temp_c: float
    scenario_type: str
    road_type: str
    weather_condition: str
    visibility_m: int
    time_of_day: str
    traffic_density: str
    nn_prediction: str
    driver_action: str
    intervened: bool
    intervention_type: str
    camera_occlusion: bool


def generate_vehicle_ids(n: int) -> list[str]:
    return [f"VH-{uuid.uuid4().hex[:6].upper()}" for _ in range(n)]


def random_coords(region: str) -> tuple[float, float]:
    lat_min, lat_max, lon_min, lon_max = REGION_COORDS[region]
    return (
        round(random.uniform(lat_min, lat_max), 6),
        round(random.uniform(lon_min, lon_max), 6),
    )


def generate_event(vehicle_id: str, base_time: datetime) -> DrivingEvent:
    region = random.choice(REGIONS)
    road_type = random.choice(ROAD_TYPES)
    weather = random.choice(WEATHER_CONDITIONS)
    scenario = random.choice(SCENARIO_TYPES)
    time_of_day = random.choice(TIMES_OF_DAY)
    traffic = random.choice(TRAFFIC_DENSITIES)

    speed_min, speed_max = ROAD_SPEED_CAPS[road_type]
    speed = round(random.uniform(speed_min, speed_max), 1)

    vis_min, vis_max = WEATHER_VISIBILITY[weather]
    visibility = random.randint(vis_min, vis_max)

    # camera occlusion more likely in bad weather
    occlusion_chance = 0.05 if weather == "clear" else 0.25
    camera_occlusion = random.random() < occlusion_chance

    nn_prediction = random.choice(NN_ACTIONS)

    # driver mostly agrees with the model, intervention rate ~8%
    if random.random() < 0.08:
        remaining = [a for a in NN_ACTIONS if a != nn_prediction]
        driver_action = random.choice(remaining)
    else:
        driver_action = nn_prediction

    intervened = nn_prediction != driver_action

    if intervened:
        if "brake" in driver_action or driver_action == "stop":
            intervention_type = "brake_override"
        else:
            intervention_type = "steering_override"
    else:
        intervention_type = "none"

    brake_pressure = 0.0
    if driver_action == "brake" or driver_action == "stop":
        brake_pressure = round(random.uniform(10.0, 95.0), 1)

    throttle = 0.0
    if driver_action == "accelerate" or driver_action == "maintain":
        throttle = round(random.uniform(10.0, 80.0), 1)

    lat, lon = random_coords(region)
    jitter_seconds = random.randint(0, 3600)
    timestamp = (base_time + timedelta(seconds=jitter_seconds)).isoformat()

    return DrivingEvent(
        vehicle_id=vehicle_id,
        timestamp=timestamp,
        fsd_version=random.choice(FSD_VERSIONS),
        hardware_version=random.choice(HARDWARE_VERSIONS),
        region=region,
        latitude=lat,
        longitude=lon,
        speed_mph=speed,
        heading_degrees=random.randint(0, 359),
        acceleration_mps2=round(random.uniform(-4.0, 4.0), 2),
        brake_pressure_pct=brake_pressure,
        throttle_pct=throttle,
        steering_angle_deg=round(random.uniform(-30.0, 30.0), 2),
        battery_level_pct=round(random.uniform(5.0, 100.0), 1),
        ambient_temp_c=round(random.uniform(-20.0, 45.0), 1),
        scenario_type=scenario,
        road_type=road_type,
        weather_condition=weather,
        visibility_m=visibility,
        time_of_day=time_of_day,
        traffic_density=traffic,
        nn_prediction=nn_prediction,
        driver_action=driver_action,
        intervened=intervened,
        intervention_type=intervention_type,
        camera_occlusion=camera_occlusion,
    )


def inject_bad_record(event: dict) -> dict:
    # randomly corrupt one field to simulate real-world data quality issues
    corruption = random.choice([
        lambda e: {**e, "vehicle_id": None},
        lambda e: {**e, "speed_mph": "FAST"},
        lambda e: {**e, "timestamp": "not-a-date"},
        lambda e: {**e, "battery_level_pct": -999},
        lambda e: {**e, "latitude": None, "longitude": None},
    ])
    return corruption(event)


def run(num_vehicles: int, num_events: int, output_dir: str, bad_record_rate: float) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    vehicle_ids = generate_vehicle_ids(num_vehicles)
    base_time = datetime.now(timezone.utc).replace(microsecond=0)

    filename = f"driving_events_{base_time.strftime('%Y%m%d_%H%M%S')}.json"
    output_path = os.path.join(output_dir, filename)

    good_count = 0
    bad_count = 0

    # write NDJSON — one record per line, efficient for BigQuery and Spark ingestion
    with open(output_path, "w") as f:
        for _ in range(num_events):
            vehicle_id = random.choice(vehicle_ids)
            event = asdict(generate_event(vehicle_id, base_time))

            if random.random() < bad_record_rate:
                event = inject_bad_record(event)
                bad_count += 1
            else:
                good_count += 1

            f.write(json.dumps(event) + "\n")

    print(f"Output:      {output_path}")
    print(f"Total:       {num_events} records")
    print(f"Clean:       {good_count}")
    print(f"Bad:         {bad_count} ({bad_count / num_events * 100:.1f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic Tesla fleet telemetry data.")
    parser.add_argument("--num-vehicles", type=int, default=50, help="Number of unique vehicles to simulate")
    parser.add_argument("--num-events", type=int, default=10000, help="Total number of events to generate")
    parser.add_argument("--output-dir", type=str, default="./data/raw", help="Directory to write output JSON file")
    parser.add_argument("--bad-record-rate", type=float, default=0.05, help="Fraction of records to corrupt (0.0 - 1.0)")
    args = parser.parse_args()

    run(
        num_vehicles=args.num_vehicles,
        num_events=args.num_events,
        output_dir=args.output_dir,
        bad_record_rate=args.bad_record_rate,
    )


if __name__ == "__main__":
    main()
