import mss

with mss.mss() as sct:
    print(f"Monitors: {sct.monitors}")
    
    try:
        sct.shot(mon=-1, output="test_all.png")
        print("Captured mon=-1 successfully")
    except Exception as e:
        print(f"Failed mon=-1: {e}")
        
    try:
        sct.shot(mon=1, output="test_mon1.png")
        print("Captured mon=1 successfully")
    except Exception as e:
        print(f"Failed mon=1: {e}")
