# Comp Agent Input Packet

The future frontend can keep the full standard project brief, then send this comp-focused packet to the Comp Agent.

Manual entry and RFP autofill should fill the same fields. RFP extraction is just an autofill step.

## Shape

```json
{
  "project_name": "200 Vesey Repositioning",
  "address": "200 Vesey Street, New York, NY",
  "program_type": "office repositioning",
  "geography": "New York City",
  "scope_summary": "Lobby, tenant amenity, retail, and public realm upgrades.",
  "design_priorities": [
    "arrival experience",
    "tenant amenities",
    "retail activation",
    "public realm"
  ],
  "comparative_projects": {
    "comp_guidance": "Prioritize recent repositioning projects with strong arrival, amenity, and public realm moves.",
    "comp_types": [
      "office lobby repositioning",
      "tenant amenity upgrade",
      "public realm activation"
    ],
    "must_include_comps": [
      {
        "name": "660 Fifth Avenue",
        "location": "New York, NY",
        "note": "Must include as lobby precedent."
      }
    ]
  }
}
```

The backend also accepts the same comp section under:

```json
{
  "agent_inputs": {
    "comparative_projects": {}
  }
}
```

That lets a larger frontend keep one full project brief and send only the comp-relevant slice to this agent.

## Behavior

- The agent searches for relevant comps using the project brief and comp guidance.
- Must-use comps are added to the candidate list.
- Duplicates between agent-found comps and must-use comps are merged.
- The user still approves the final candidate list before the deeper research and deck build.
- The current proof-of-concept UI remains supported through its existing fields.

## Output Handoff

The Comp Agent should return its own deck and file metadata. The final compiler can later stitch this deck together with other agent decks.

```json
{
  "agent_id": "comparative_projects",
  "section_title": "Comparative Projects",
  "status": "complete",
  "deck_path": "outputs/comp_study_deck.pptx",
  "sort_order": 30
}
```
