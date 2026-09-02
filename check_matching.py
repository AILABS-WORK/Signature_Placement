"""Check run.log: every stamped doc got the signature matching its account name."""
import re
from pathlib import Path

d = Path(__file__).parent
expected = {0: "Stonehage Corient Ltd.png", 1: "Corient Advisory SA.png",
            2: "Corient Fleming Advisory (Monaco).png"}
ok = bad = 0
cur = None
raw = (d / "run.log").read_bytes()
text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
for line in text.splitlines():
    m = re.match(r"doc_(\d+)\.pdf:", line)
    if m:
        cur = int(m.group(1))
        continue
    m = re.search(r"stamped '(.+?)'", line)
    if m:
        want = expected[cur % 3]
        if m.group(1) == want:
            ok += 1
        else:
            bad += 1
            print("WRONG", cur, m.group(1), "wanted", want)
print(f"correct signature: {ok}/{ok + bad}")
