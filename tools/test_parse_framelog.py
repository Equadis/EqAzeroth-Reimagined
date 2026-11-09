"""Test for parse_framelog.py with a SYNTHETIC log (the numbers are made up; the line formats are the ones ComfyAtmosphere's
source prints). run: python -I tools/test_parse_framelog.py"""
import os, subprocess, sys, tempfile

LOG = """framelog: 3000 frames over 30.0 s: 10.00 ms a frame (100.0 fps), slowest 1% 14.00 ms, worst 22.00 ms
framelog: our CPU in all a frame 4.00 ms: 3.50 ms in the hooks on the client's draws (the water 0.10 ms of it), 0.50 ms in our passes (the shadow maps 0.10 ms)
framelog: the grass's CPU a frame: 0.12 ms, its draws included
framelog: our GPU a frame 1.00 ms: shadow maps 0.10; the GPU's whole frame 7.50 ms (2900 frames timed)
grass: on; last frame 213 draws, 130516 vertices, 0 left out with a pixel shader, 0 off the chunks' grid
grass: dense: density 1.00 to 45 yards, spread 1.20; 0 extra draws last frame (0 batches without ground slope); the copy shader not needed (density 1); ground slopes cached 0
framelog: 2800 frames over 30.0 s: 10.71 ms a frame (93.3 fps), slowest 1% 15.00 ms, worst 30.00 ms
framelog: the grass's CPU a frame: 0.52 ms, its draws included
grass: on; last frame 213 draws, 130516 vertices, 0 left out with a pixel shader, 0 off the chunks' grid
grass: dense: density 3.00 to 45 yards, spread 1.20; 410 extra draws last frame (12 batches without ground slope); the copy shader ready; ground slopes cached 96
framelog: 100 frames over 3.0 s: 10.00 ms a frame (100.0 fps), slowest 1% 14.00 ms, worst 22.00 ms
"""
here = os.path.dirname(os.path.abspath(__file__))
with tempfile.TemporaryDirectory() as t:
    p = os.path.join(t, "c.log")
    open(p, "w").write(LOG)
    out = subprocess.run([sys.executable, "-I", os.path.join(here, "parse_framelog.py"), p], capture_output=True, text=True).stdout
rows = [l for l in out.splitlines() if l.startswith("| ") and not l.startswith("| run") and "---" not in l]
assert len(rows) == 2, out
assert "| 3.00 | 45 | 10.71 | 93.3 | 15.00 | 30.00 | 0.52 | +0.40 | +0.71 | 410 | 213 |" in rows[1], rows[1]
assert "1 framelog block(s) had no" in out
print("parse_framelog: ok")
