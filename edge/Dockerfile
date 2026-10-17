# Render demo edge: one public origin that routes /api/* to the backend and everything else to the frontend.
# Mirrors docker/Caddyfile (compose). Render terminates TLS; Caddy listens on $PORT (plain HTTP).
FROM caddy:2.8-alpine
COPY Caddyfile /etc/caddy/Caddyfile
