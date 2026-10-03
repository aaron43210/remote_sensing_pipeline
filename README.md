# Hyperspectral Remote Sensing Pipeline

Welcome to the Hyperspectral Remote Sensing Pipeline project. This repository contains a microservices-based architecture designed for acquiring, processing, and analyzing hyperspectral and thermal imagery data.

## 🏗 Architecture Overview

The system is composed of several specialized microservices, each handling a distinct part of the pipeline:

*   **API Gateway (`/api_gateway`)**: The main entry point for client applications. Built in Go, it handles authentication, request routing, and status polling.
*   **Frontend (`/frontend`)**: A React (Vite) application providing an interactive dashboard for map viewing, mineral analysis, and agricultural insights.
*   **Data Ingestion (`/ingestion`)**: A Python service responsible for fetching raw remote sensing data from external sources and preparing it for the pipeline.
*   **Preprocessing (`/preprocessing`)**: A Python service that performs essential data corrections (e.g., atmospheric correction) before the data reaches the machine learning models.
*   **ML Inference (`/ml_inference`)**: The core analytical engine written in Python. It runs the machine learning models to extract meaningful insights from the preprocessed hyperspectral data.
*   **Orchestrator (`/orchestrator`)**: A Go-based service that coordinates the entire workflow, managing the flow of data between ingestion, preprocessing, and inference stages.
*   **Infrastructure (`/infra`)**: Contains necessary infrastructure configuration, including the database schema (`schema.sql`).

## 🚀 Getting Started

The entire pipeline is containerized and orchestrated via Docker Compose.

### Prerequisites
* [Docker](https://docs.docker.com/get-docker/)
* [Docker Compose](https://docs.docker.com/compose/install/)

### Running Locally

1. **Setup Environment Variables**:
   Copy `.env.example` to `.env` and fill in any required API keys or secrets.
   ```bash
   cp .env.example .env
   ```

2. **Start the Services**:
   Run the following command in the root of the repository to build and start all containers:
   ```bash
   docker-compose up --build
   ```

3. **Access the Application**:
   Once all services are up and running, you can access the frontend dashboard through your browser (usually `http://localhost:5173` or as configured). The API Gateway is exposed for backend interactions.

## 🛠 Tech Stack
* **Go**: API Gateway, Orchestrator
* **Python**: Ingestion, Preprocessing, ML Inference
* **React + Vite**: Frontend Dashboard
* **Docker & Docker Compose**: Containerization and Orchestration
