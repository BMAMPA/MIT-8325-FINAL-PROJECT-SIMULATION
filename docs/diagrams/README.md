# Report diagrams (Graphviz source)

All architecture and workflow diagrams in the report were produced with **Graphviz**
(https://graphviz.org), an open-source graph-drawing tool. Each `.dot` file is the text
source of one figure; edit it and re-render with:

```bash
dot -Tpng figure2_five_layer_architecture.dot -o figure2.png
```

| File | Report figure |
|---|---|
| figure1_conceptual_framework.dot | Figure 1 — Conceptual framework |
| figure2_five_layer_architecture.dot | Figure 2 — Five-layer reference architecture |
| figure3_transaction_workflow.dot | Figure 3 — Transaction workflow and validation gates |
| figure7_security_boundary.dot | Figure 7 — Security boundary flow |
| figure8_defence_in_depth.dot | Figure 8 — Defence-in-depth security layers |
| diagram1_entity_relationship.dot | Diagram 1 — Entity-relationship model |
| diagram2_api_components.dot | Diagram 2 — API component interface |
| diagram3_pilot_workflow.dot | Diagram 3 — Pilot transaction workflow |

Figures 6 and A1 are screenshots of the running simulations captured in Google Chrome.
Figures 4 and 5 come from the student's original draft.
