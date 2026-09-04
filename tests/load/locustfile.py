# =============================================================
# OWNER: AARON
# =============================================================
from locust import HttpUser, task, between
import random


class HyperspectralAPIUser(HttpUser):
    """
    Load testing for production API.
    Run with: locust -f tests/load/locustfile.py --host http://localhost:8000
    """
    wait_time = between(1, 3)

    @task(5)
    def check_health(self):
        self.client.get("/health")

    @task(3)
    def get_status(self):
        scene_id = f"SCENE_{random.randint(1, 100):04d}"
        self.client.get(f"/status/{scene_id}")

    @task(2)
    def submit_processing(self):
        scene_id = f"SCENE_{random.randint(1000, 9999)}"
        self.client.post(
            "/process",
            json={"scene_id": scene_id}
        )

    @task(1)
    def get_metrics(self):
        self.client.get("/metrics")
