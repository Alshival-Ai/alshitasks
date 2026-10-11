"""Killable worker: bounded native probes cannot stall the scheduler."""

import json
import sys
from alshitasks.monitoring import probe

print(json.dumps(probe(json.loads(sys.stdin.read(8192)))))
