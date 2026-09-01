import pathlib
import re

p1 = pathlib.Path("backend/app/stt/service.py").read_text(encoding="utf-8")
p2 = pathlib.Path("backend/app/websocket/gateway.py").read_text(encoding="utf-8")

events = set(re.findall(r'"event":\s*"([^"]+)"', p1 + p2))
print("Events:")
for e in sorted(events):
    print(" -", e)
