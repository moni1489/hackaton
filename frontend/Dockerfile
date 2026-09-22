FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
# Адрес бэкенда вшивается в сборку: браузер ходит туда напрямую (CORS открыт).
ARG VITE_API_URL=http://localhost:8000
RUN VITE_API_URL=$VITE_API_URL npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
