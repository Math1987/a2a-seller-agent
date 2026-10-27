#!/bin/bash
# A2A/ngrok watchdog. Never logs the authtoken.
D=/workspace/a2a; cd $D
log(){ echo "$(date '+%F %T %Z') $*" >> $D/watchdog.log; }
start_server(){ [ -f server.pid ] && kill $(cat server.pid) 2>/dev/null; sleep 1
  setsid nohup $D/.venv/bin/python $D/server.py >> $D/server.log 2>&1 < /dev/null & echo $! > server.pid; sleep 2; log "server started pid $(cat server.pid)"; }
start_ngrok(){ [ -f ngrok.pid ] && kill $(cat ngrok.pid) 2>/dev/null; pkill -f "$D/ngrok http 9999" 2>/dev/null; pkill -x ngrok 2>/dev/null; sleep 2
  setsid nohup $D/ngrok http 9999 --config /home/box/.config/ngrok/ngrok.yml,$D/ngrok_extra.yml --log=stdout >> $D/ngrok.log 2>&1 < /dev/null & echo $! > ngrok.pid
  for i in $(seq 1 20); do sleep 1; U=$(curl -s 127.0.0.1:4040/api/tunnels | python3 -c 'import sys,json;print([t["public_url"] for t in json.load(sys.stdin)["tunnels"] if t["public_url"].startswith("https")][0])' 2>/dev/null); [ -n "$U" ] && break; done
  [ -z "$U" ] && { log "ngrok: no url"; return 1; }
  echo -n "${U%/}" > public_base_url.txt; log "ngrok pid $(cat ngrok.pid) url $U"; start_server; }
okjson(){ curl -s -m 10 -o /tmp/wd.json -w '%{http_code}' "$@" | grep -q 200 && python3 -m json.tool /tmp/wd.json >/dev/null 2>&1; }
[ "$1" = "--restart-now" ] && { start_ngrok; exit; }
F=0; log "watchdog start pid $$"
while true; do
  okjson http://localhost:9999/.well-known/agent-card.json || { log "local down, restart"; start_server; }
  if okjson -H 'ngrok-skip-browser-warning: 1' "$(cat public_base_url.txt)/.well-known/agent-card.json"; then F=0; else F=$((F+1)); log "public check failed ($F)"; fi
  [ $F -ge 2 ] && { log "restarting ngrok"; start_ngrok; F=0; }
  sleep 20
done
