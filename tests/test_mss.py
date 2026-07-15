import mss
with mss.mss() as sct:
    filename = sct.shot(output="test_mss.png")
    print(f"Saved {filename}")
