# --- Build stage -------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /app

# Vite env vars are baked into the JS bundle at build time, so this must be
# a build ARG (passed via `docker build --build-arg VITE_API_BASE_URL=...`
# or a docker-compose `build.args` entry), not a runtime ENV.
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

# No package-lock.json is committed (this repo was built without local npm
# access to generate one), so `npm install` is used instead of `npm ci`.
# If a lockfile is added later, switch this back to `npm ci` for reproducible,
# faster builds.
COPY package.json ./
RUN npm install

COPY . .
RUN npm run build

# --- Serve stage ---------------------------------------------------------
FROM nginx:alpine AS serve

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
