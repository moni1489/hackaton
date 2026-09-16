import time
import random
import threading
import requests
import subprocess
import json
from datetime import datetime

# Hackathon: Use local backend for testing
BACKEND_URL = "http://localhost:8000"
SCHOOL_ID = 1
DEVICE_ID = "PC-KABINET-12"

# For hackathon demo, test every 60 seconds instead of hours
TEST_INTERVAL_MIN = 60
TEST_INTERVAL_MAX = 120

def measure_speed():
    # We use speedtest-cli via subprocess to get json output
    print("Measuring speed...")
    try:
        result = subprocess.run(['speedtest-cli', '--json'], capture_output=True, text=True)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return {
                "download_speed": round(data['download'] / 1_000_000, 2), # Convert to Mbps
                "upload_speed": round(data['upload'] / 1_000_000, 2),
                "ping": round(data['ping'], 2),
                "jitter": random.uniform(2, 10), # Fake jitter for demo
                "packet_loss": 0.0,
                "is_offline": False
            }
    except Exception as e:
        print(f"Error measuring speed: {e}")
        
    # If it fails, report offline
    return {
        "download_speed": 0,
        "upload_speed": 0,
        "ping": 0,
        "jitter": 0,
        "packet_loss": 100,
        "is_offline": True
    }

def background_task():
    # Register device
    try:
        requests.post(f"{BACKEND_URL}/api/devices/register", json={
            "device_id": DEVICE_ID,
            "school_id": SCHOOL_ID,
            "name": "Teacher PC",
            "room": "Kabinet 301"
        })
        print("Device registered successfully.")
    except Exception as e:
        print(f"Failed to register device: {e}")

    while True:
        # Wait for a random interval
        interval = random.randint(TEST_INTERVAL_MIN, TEST_INTERVAL_MAX)
        print(f"Next test in {interval} seconds...")
        time.sleep(interval)
        
        # Test speed
        metrics = measure_speed()
        metrics["device_id"] = DEVICE_ID
        metrics["school_id"] = SCHOOL_ID
        metrics["timestamp"] = datetime.now().isoformat()
        
        print(f"Results: {metrics}")
        
        # Send to backend
        try:
            response = requests.post(f"{BACKEND_URL}/api/measurements", json=metrics)
            print(f"Backend response: {response.status_code}")
        except Exception as e:
            print(f"Failed to send to backend (would cache locally): {e}")

def main():
    print("Starting agent in console mode...")
    background_task()

if __name__ == "__main__":
    main()
