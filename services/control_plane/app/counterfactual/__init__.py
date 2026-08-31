"""The Judge Mode counterfactual and the model probe -- sections 8.30, 8.31, 8.33.

Three port methods live behind this package: ``write.start_counterfactual``,
``write.get_counterfactual`` and ``write.run_probe``. All three were in
``app/api/adapters/unbound.py`` waiting on "the agent runtime" and "the model
router", and both now exist -- the graph in
``agents/runtime/graphs/counterfactual_graph.py``, the router in
``agents/runtime/model_router/``.

Module map
----------
``service.py``    orchestration: two walks of one graph, the parity block, the
                  measured safety block.
``sql.py``        the database boundary, over the app pool.
``store.py``      the two ``agent_runs`` statements and the pair read.
``artifacts.py``  the artifact bytes, found by the digest the row records.
``probe.py``      section 8.33.
"""

from __future__ import annotations

__all__: list[str] = []
