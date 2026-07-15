import time
import json
import os
from dotenv import load_dotenv
import google.generativeai as genai
import PIL.Image
import numpy as np
from src.desktop.capture import CaptureEngine
from src.ocr_locator import extract_ocr_data, find_text
from src.lesson_engine import LessonEngine, parse_lesson_steps

def run_benchmark(iterations=5):
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    genai.configure(api_key=api_key)

    capture_engine = CaptureEngine()
    
    # Store metrics
    capture_times = []
    ocr_times = []
    api_times = []
    render_times = []
    e2e_times = []
    
    ocr_matched = 0
    ocr_total = 0

    print(f"Starting Benchmark for {iterations} iterations...")
    
    question = "Can you explain the optimal approach for this problem?"

    for i in range(iterations):
        print(f"--- Iteration {i+1} ---")
        t0 = time.perf_counter()

        # 1. Capture Time
        t_cap_start = time.perf_counter()
        image_path = capture_engine.capture("screen")
        t_cap_end = time.perf_counter()
        capture_times.append((t_cap_end - t_cap_start) * 1000)

        # 2. OCR Time
        t_ocr_start = time.perf_counter()
        ocr_data = extract_ocr_data(image_path)
        t_ocr_end = time.perf_counter()
        ocr_times.append((t_ocr_end - t_ocr_start) * 1000)

        # 3. Gemini API Time
        t_api_start = time.perf_counter()
        model = genai.GenerativeModel("gemini-3.1-flash-lite")
        img = PIL.Image.open(image_path)
        
        engine = LessonEngine(image_path, ocr_data)
        prompt = engine.build_lesson_prompt(question, "", "No previous explanation.")
        
        try:
            response = model.generate_content([prompt, img])
            response_text = response.text
        except Exception as e:
            print(f"API failed: {e}")
            continue
            
        t_api_end = time.perf_counter()
        api_times.append((t_api_end - t_api_start) * 1000)

        # 4. Rendering / OCR Matching Time
        t_render_start = time.perf_counter()
        
        parsed_steps = parse_lesson_steps(response_text)
        for step in parsed_steps:
            anchor = step.get("anchor")
            context = step.get("context")
            if anchor and anchor.upper() != "NONE":
                ocr_total += 1
                try:
                    res = find_text(ocr_data, anchor, context)
                    if res:
                        ocr_matched += 1
                except Exception:
                    pass
        
        t_render_end = time.perf_counter()
        render_times.append((t_render_end - t_render_start) * 1000)

        # End to End
        t_end = time.perf_counter()
        e2e_times.append((t_end - t0) * 1000)
        
        # Rate limit protection (15 RPM free tier limit)
        if i < iterations - 1:
            print("Sleeping for 4 seconds to respect Gemini API rate limits...")
            time.sleep(4)

    print("\n\n================ RESULTS ================")
    
    def get_stats(data):
        if not data: return {}
        return {
            "avg": float(np.mean(data)),
            "med": float(np.median(data)),
            "p95": float(np.percentile(data, 95)),
            "max": float(np.max(data))
        }
        
    results = {
        "iterations": iterations,
        "capture": get_stats(capture_times),
        "ocr": get_stats(ocr_times),
        "api": get_stats(api_times),
        "render": get_stats(render_times),
        "e2e": get_stats(e2e_times),
        "accuracy": (ocr_matched / ocr_total) if ocr_total > 0 else 0,
        "ocr_matched": ocr_matched,
        "ocr_total": ocr_total
    }
    
    with open("benchmark_results.json", "w") as f:
        json.dump(results, f, indent=4)
        
    print(f"Results saved to benchmark_results.json")
    
    # Generate Chart if matplotlib is installed
    try:
        import matplotlib.pyplot as plt
        categories = ["Capture", "OCR", "Gemini API", "Render", "End-to-End"]
        avgs = [results["capture"]["avg"], results["ocr"]["avg"], results["api"]["avg"], results["render"]["avg"], results["e2e"]["avg"]]
        
        plt.figure(figsize=(10, 6))
        bars = plt.bar(categories, avgs, color=['#3498db', '#e74c3c', '#f1c40f', '#2ecc71', '#9b59b6'])
        plt.title('Average Pipeline Latency (30 Iterations)')
        plt.ylabel('Time (ms)')
        for bar in bars:
            yval = bar.get_height()
            plt.text(bar.get_x() + bar.get_width()/2, yval + 100, f"{int(yval)}ms", ha='center', va='bottom')
            
        plt.savefig("benchmark_chart.png")
        print("Chart saved to benchmark_chart.png")
    except ImportError:
        print("Matplotlib not installed. Skipping chart generation.")

if __name__ == "__main__":
    run_benchmark(30)
