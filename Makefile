.PHONY: all build up down test benchmark clean logs

# ── Development ──────────────────────────────
dev-up:
	docker-compose up -d
	@echo "✅ Development environment started"
	@echo "   API:       http://localhost:8000/docs"
	@echo "   Dashboard: http://localhost:8501"
	@echo "   MinIO:     http://localhost:9001"

dev-down:
	docker-compose down
	@echo "✅ Development environment stopped"

# ── Production ───────────────────────────────
prod-build:
	docker-compose -f docker-compose.production.yml build
	@echo "✅ Production images built"

prod-up:
	docker-compose -f docker-compose.production.yml up -d
	@echo "✅ Production environment started"

prod-down:
	docker-compose -f docker-compose.production.yml down

# ── Testing ──────────────────────────────────
test-unit:
	pytest tests/unit/ -v --tb=short
	@echo "✅ Unit tests complete"

test-integration:
	pytest tests/integration/ -v --tb=short
	@echo "✅ Integration tests complete"

test-load:
	locust -f tests/load/locustfile.py \
		--host http://localhost:8000 \
		--users 50 \
		--spawn-rate 5 \
		--run-time 60s \
		--headless
	@echo "✅ Load tests complete"

test-all: test-unit test-integration
	@echo "✅ All tests complete"

# ── Benchmarking ─────────────────────────────
benchmark:
	python tests/benchmark/run_benchmark.py
	@echo "✅ Benchmark complete - check benchmark_report.md"

# ── MLOps ────────────────────────────────────
train-model:
	python services/ml/train_lightweight.py
	@echo "✅ Model training complete"

check-drift:
	python -c "from monitoring.drift_detection import DriftDetector; \
		d = DriftDetector(); print(d.check_production_drift())"

# ── Kubernetes ───────────────────────────────
k8s-deploy:
	kubectl apply -f infrastructure/kubernetes/namespace.yaml
	kubectl apply -f infrastructure/kubernetes/configmap.yaml
	kubectl apply -f infrastructure/kubernetes/deployments.yaml
	kubectl apply -f infrastructure/kubernetes/services.yaml
	kubectl apply -f infrastructure/kubernetes/hpa.yaml
	@echo "✅ Kubernetes deployment complete"

k8s-status:
	kubectl get pods -n hyperspectral
	kubectl get services -n hyperspectral

k8s-logs:
	kubectl logs -n hyperspectral -l app=$(SERVICE) --tail=100

# ── Utilities ────────────────────────────────
logs:
	docker-compose logs -f $(SERVICE)

clean:
	docker-compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✅ Cleanup complete"

validate-data:
	python -c "from data_quality.validator import HyperspectralDataValidator; \
		v = HyperspectralDataValidator(); \
		import numpy as np; \
		cube = np.random.rand(256, 256, 200); \
		wl = list(range(400, 2501, 11)); \
		print(v.validate_scene(cube, wl))"

health-check:
	curl -s http://localhost:8000/health | python -m json.tool

# ── Infrastructure (Terraform) ───────────────
tf-init:
	cd infrastructure/terraform && terraform init

tf-plan:
	cd infrastructure/terraform && terraform plan

tf-apply:
	cd infrastructure/terraform && terraform apply -auto-approve

# ── Spark Batch ──────────────────────────────
run-spark:
	docker-compose -f docker-compose.production.yml exec spark_batch python batch_processor.py
