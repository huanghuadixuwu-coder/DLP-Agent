# Two Pointers

Two pointers are useful when an array or string can be scanned from one or two directions while preserving an invariant.

Use this pattern when:
- the input is already sorted
- you need to shrink a window
- you need to compare pairs without nested loops

Common tradeoffs:
- time complexity often improves from O(n^2) to O(n)
- space complexity often stays O(1)
- the pointer movement rule must be explicit or bugs appear quickly

