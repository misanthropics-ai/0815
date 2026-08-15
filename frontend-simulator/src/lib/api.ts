import type { DecisionResult, ProductRef } from "../../../contracts/types";

const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");

export type StreamEvent = {
  event: string;
  data: Record<string, unknown>;
};

export async function* readSse(response: Response): AsyncGenerator<StreamEvent> {
  if (!response.body) throw new Error("The server returned an empty stream.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const chunk = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      let event = "message";
      let data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) yield { event, data: JSON.parse(data) as Record<string, unknown> };
      split = buffer.indexOf("\n\n");
    }
  }
}

export async function simulate(
  intent: { text: string; cluster_id: string; attributes: string[] },
  candidates: ProductRef[],
  onToken: (text: string) => void,
): Promise<DecisionResult> {
  const response = await fetch(`${API_BASE}/simulate`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify({ intent, candidates, stream: true, cached: true, mode: "mock" }),
  });
  if (!response.ok) throw new Error(`Simulation failed (${response.status}).`);

  let decision: DecisionResult | undefined;
  for await (const event of readSse(response)) {
    if (event.event === "token") onToken(String(event.data.text || ""));
    if (event.event === "error") throw new Error(String(event.data.message || "Simulation failed."));
    if (event.event === "done") decision = event.data.decision as DecisionResult;
  }
  if (!decision) throw new Error("The simulation ended without a decision.");
  return decision;
}

export async function compare(
  before: ProductRef,
  after: ProductRef,
  cluster: string,
): Promise<Record<string, unknown>> {
  const response = await fetch(
    `${API_BASE}/metrics/compare?a=${encodeURIComponent(before)}&b=${encodeURIComponent(after)}&cluster=${encodeURIComponent(cluster)}`,
  );
  if (response.status === 202) throw new Error("Comparison is still running.");
  if (!response.ok) throw new Error(`Comparison failed (${response.status}).`);
  return (await response.json()) as Record<string, unknown>;
}
