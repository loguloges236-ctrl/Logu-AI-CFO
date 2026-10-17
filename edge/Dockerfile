# Render demo edge: one public origin. /api/* goes to logu-backend, everything else to logu-frontend.
# nginx replaces Caddy here: the Caddy binary failed to exec on Render ("operation not permitted").
# Render terminates TLS; nginx listens on 10000 (plain HTTP) and talks to the upstreams over HTTPS.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 10000
