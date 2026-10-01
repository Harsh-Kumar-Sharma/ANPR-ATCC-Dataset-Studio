# Proxy settings for studio.highwaynetra.in, shared by the :80 and :443
# blocks. INSTALLED AT: /app/mlff-node/nginx/sites/studio-proxy.inc
# (not *.conf, so the sites/*.conf include does not load it on its own).
# See studio.conf.

proxy_pass http://172.18.0.1:8090;
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;

# Studio streams: the live preview (polled, must not lag), the source
# video (Range requests) and the dataset archive (a zip built while it
# downloads, minutes long). Buffering off and long timeouts for all of
# it; Studio's own nginx does the per-path tuning behind this.
proxy_buffering off;
proxy_request_buffering off;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
client_max_body_size 50m;
