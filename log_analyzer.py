import re
import json

def analyze_logs(logfile="stress_test.log"):
    try:
        with open(logfile, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Could not find {logfile}")
        return

    # Split the log by test blocks
    tests = re.split(r"--- Test \d+/\d+ ---", content)[1:]
    
    stats = {
        "total_tests": len(tests),
        "successes": 0,
        "failures": {
            "timeout_504": 0,
            "rate_limit_429": 0,
            "ocean_remote": 0,
            "empty_desert": 0,
            "other": 0
        },
        "successful_recipes": []
    }

    for test in tests:
        # Extract Users
        users_match = re.search(r"User 1: (.+?) \| User 2: (.+?)\n", test)
        if not users_match: continue
        city1, city2 = users_match.groups()
        
        # Check Success or Failure
        if "✅ Test Passed" in test:
            stats["successes"] += 1
            # Extract venues
            venues = re.findall(r"\d+\.\s+(.+)", test)
            stats["successful_recipes"].append({
                "city1": city1,
                "city2": city2,
                "venues": venues
            })
        else:
            if "504 Gateway Timeout" in test:
                stats["failures"]["timeout_504"] += 1
            elif "429 Too Many Requests" in test:
                stats["failures"]["rate_limit_429"] += 1
            elif "too remote/ocean" in test:
                stats["failures"]["ocean_remote"] += 1
            elif "I couldn't find any cafes or bars" in test:
                stats["failures"]["empty_desert"] += 1
            else:
                stats["failures"]["other"] += 1

    # Print Report
    print("📊 STRESS TEST DIAGNOSTIC REPORT")
    print("="*40)
    print(f"Total Tests Run: {stats['total_tests']}")
    print(f"Total Successes: {stats['successes']}")
    print(f"Total Failures:  {stats['total_tests'] - stats['successes']}\n")
    
    print("📉 FAILURE BREAKDOWN:")
    print(f" - 504 Gateway Timeouts (Server Overload): {stats['failures']['timeout_504']}")
    print(f" - 429 Too Many Requests (Rate Limited):   {stats['failures']['rate_limit_429']}")
    print(f" - Ocean / Too Remote (Math failed):       {stats['failures']['ocean_remote']}")
    print(f" - Empty Desert (No venues in 30km):       {stats['failures']['empty_desert']}")
    print(f" - Other / Unknown Errors:                 {stats['failures']['other']}\n")
    
    print("🌟 RECREATING SUCCESSES:")
    print("Here are known-good city combinations you can use for your presentation:")
    for recipe in stats["successful_recipes"][:5]: # show first 5
        print(f" - {recipe['city1']} + {recipe['city2']} -> Found {len(recipe['venues'])} venues")

    # Save successes to JSON for the "perpetuity" aspect
    with open("success_recipes.json", "w", encoding="utf-8") as f:
        json.dump(stats["successful_recipes"], f, indent=4)
    print("\n💾 All successful recipes saved to 'success_recipes.json' for future use/training!")

if __name__ == "__main__":
    analyze_logs()
