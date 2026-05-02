# Graph Search

Graph search uses BFS or DFS to explore reachable states.

Use this pattern when:
- you need shortest path in an unweighted graph
- you need reachability or connected components
- a state graph is implicit rather than explicitly stored

Common tradeoffs:
- BFS is good for shortest unweighted steps
- DFS is simpler for exhaustive search
- visited-state tracking is essential to avoid repeats

