import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App, debateSessionId, matchingAction } from "./App";

describe("P4 shopper simulator", () => {
  it("renders the complete default before/after demo without a runtime crash", () => {
    const html = renderToString(<App />);

    expect(html).toContain("Measure what the new evidence changed");
    expect(html).toContain("Deterministic evidence");
    expect(html).toContain("Impact test");
    expect(html).toContain("CabinZero");
    expect(html).toContain("Osprey");
    expect(html).toContain("Same buyer. New evidence.");
    expect(html).toContain("Run both versions");
  });

  it("recovers the persisted P5 debate action without changing P5", () => {
    expect(debateSessionId({ change_note: "debate:dbt_123" } as never)).toBe("dbt_123");
    const action = matchingAction([
      {
        action_offer: {
          type: "create_version_and_rerun",
          status: "started",
          params: { additions: ["New evidence"], cluster_id: "comfort_carry" },
          base_ref: "target@v1",
          new_ref: "target@v2",
          batch_a: "batch_before",
          batch_b: "batch_after",
        },
      },
    ], "target@v1", "target@v2");

    expect(action?.batch_a).toBe("batch_before");
    expect(action?.batch_b).toBe("batch_after");
  });
});
