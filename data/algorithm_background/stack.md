# Stack

A stack models last-in-first-out behavior and is useful for matching, monotonic scans, and nested structures.

Use this pattern when:
- you need to undo or match the most recent opening state
- nested pairs must be validated
- a monotonic condition should be preserved

Common tradeoffs:
- time complexity is usually O(n)
- space complexity is O(n) in the worst case
- edge cases often come from empty stack access

