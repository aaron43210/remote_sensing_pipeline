# =============================================================
# OWNER: AARON
# =============================================================
# Build stage
FROM node:18-alpine AS build

WORKDIR /app
RUN apt-get update && apt-get install -y gcc g++ python3-dev && rm -rf /var/lib/apt/lists/*

# Copy package files and install dependencies
COPY services/frontend/package.json services/frontend/package-lock.json ./
RUN npm ci

# Copy source code and build
COPY shared/ /app/shared/
COPY services/frontend/ /app/
RUN npm run build

# Serve stage
FROM nginx:alpine

# Copy built assets to Nginx
COPY --from=build /app/dist /usr/share/nginx/html

# Copy custom nginx config if necessary (using default for now)
# Expose port 80
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
