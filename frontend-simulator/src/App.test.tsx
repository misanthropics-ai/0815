import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("P4 shopper simulator", () => {
  it("renders the complete default before/after demo without a runtime crash", () => {
    const html = renderToString(<App />);

    expect(html).toContain("Make the invisible");
    expect(html).toContain("CabinZero");
    expect(html).toContain("Osprey");
    expect(html).toContain("Same buyer. New evidence.");
    expect(html).toContain("Run comparison");
  });
});
