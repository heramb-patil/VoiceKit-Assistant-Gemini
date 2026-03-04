# VoiceKit SaaS Frontend — multi-stage build
FROM node:18-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --silent
COPY . .
RUN npm run build

# Serve the static build with nginx
FROM nginx:alpine
COPY --from=builder /app/build /usr/share/nginx/html
# SPA rewrite: all routes → index.html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
