import asyncio
import json
import logging
from pprint import pprint

# Configure logging to stdout
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# We can import the fallback_parse_action from llm/app.py
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "llm")))

from app import fallback_parse_action, ACTION_PATTERN

def test_action_parser():
    print("=== Testing Action Parser ===")
    
    test_cases = [
        (
            "Proper action tags",
            """Sure, I can help with that.
<action>{"type": "create_event", "name": "Test Event", "description": "Desc", "location": "123 Main St", "start_time": "2025-06-15T19:00:00", "end_time": "2025-06-15T21:00:00"}</action>""",
            True
        ),
        (
            "Missing action tags, bare JSON",
            """I've scheduled it!
{"type": "create_event", "name": "Test Event", "description": "Desc", "location": "123 Main St", "start_time": "2025-06-15T19:00:00", "end_time": "2025-06-15T21:00:00"}
Have fun!""",
            True
        ),
        (
            "Wrong casing tags",
            """Here is the event:
<Action>{"type": "create_event", "name": "Test Event", "description": "Desc", "location": "123 Main St", "start_time": "2025-06-15T19:00:00", "end_time": "2025-06-15T21:00:00"}</Action>""",
            True
        ),
        (
            "Markdown json block",
            """Done.
```json
{"type": "create_event", "name": "Test Event", "description": "Desc", "location": "123 Main St", "start_time": "2025-06-15T19:00:00", "end_time": "2025-06-15T21:00:00"}
```
Let me know if you need anything else.""",
            True
        ),
        (
            "No event",
            "I don't have enough info to schedule that. Where do you want to go?",
            False
        )
    ]
    
    passed = 0
    for name, text, expect_success in test_cases:
        action = None
        match = ACTION_PATTERN.search(text)
        if match:
            try:
                action = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        
        if not action:
            action = fallback_parse_action(text)
            
        success = action is not None and action.get("type") == "create_event"
        
        if success == expect_success:
            print(f"✅ {name}")
            passed += 1
        else:
            print(f"❌ {name} (Expected {expect_success}, got {success})")
            if action:
                print(f"Parsed action: {action}")
                
    print(f"Parser tests: {passed}/{len(test_cases)} passed.\n")

if __name__ == "__main__":
    test_action_parser()
