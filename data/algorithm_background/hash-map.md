# Hash Map

A hash map is helpful when you need fast lookup from value to index, frequency, or state.

Use this pattern when:
- you need O(1) average lookup
- you need to remember elements seen earlier
- you need frequency counting

Common tradeoffs:
- time complexity is often O(n)
- space complexity increases to O(n)
- duplicate handling and update order matter

