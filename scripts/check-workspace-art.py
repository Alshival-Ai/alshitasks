"""Verify the desktop pack and, when supplied, the portal use the same artwork contract."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--portal', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
renderer = root / 'desktop/renderer'
service = root / 'service/alshitasks'
for filename in ('workspace-icons.json', 'workspace-icons.svg'):
    assert (renderer / filename).read_bytes() == (service / 'assets' / filename).read_bytes(), filename
    if args.portal:
        assert (renderer / filename).read_bytes() == (args.portal / 'customer_hub/static/customer_hub' / filename).read_bytes(), filename
if args.portal:
    assert (service / 'workspace_art.py').read_bytes() == (args.portal / 'customer_hub/workspace_art.py').read_bytes()
print('Workspace artwork parity passed.')
