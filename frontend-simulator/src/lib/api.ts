import type {
  ComparePending,
  CompareResult,
  DecisionResult,
  Intent,
  Product,
  ProductRef,
} from "../../../contracts/types";

const configuredBase = import.meta.env.VITE_API_BASE?.trim().replace(/\/$/, "");
export const API_BASE = configuredBase || (import.meta.env.DEV ? "http://localhost:8000" : "");
export const apiAvailable = Boolean(API_BASE);

const configuredMode = import.meta.env.VITE_API_MODE;
const apiMode = configuredMode === "mock" || configuredMode === "live" ? configuredMode : undefined;
const useCache = import.meta.env.VITE_USE_CACHE !== "false";

export type StreamEvent = {
  event: string;
  data: Record<string, unknown>;
};

type CompareResponse =
  | { pending: false; data: CompareResult }
  | { pending: true; data: ComparePending };

function urlFor(path: string): string {
  if (!apiAvailable) throw new Error("No API is configured. Set VITE_API_BASE to enable live mode.");
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

async function jsonRequest<T>(path: string): Promise<T> {
  const response = await fetch(urlFor(path), { headers: { accept: "application/json" } });
  const data = await response.json();
  if (!response.ok) {
    const message = data?.error?.message || `Request failed (${response.status}).`;
    throw new Error(message);
  }
  return data as T;
}

export async function getProduct(ref: ProductRef): Promise<Product> {
  return jsonRequest<Product>(`/products/${encodeURIComponent(ref)}`);
}

export async function listProducts(): Promise<Product[]> {
  const result = await jsonRequest<{ products: Product[] }>("/products");
  return result.products;
}

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
  intent: Intent,
  candidates: ProductRef[],
  onToken: (text: string) => void,
  options: { cached?: boolean } = {},
): Promise<DecisionResult> {
  const response = await fetch(urlFor("/simulate"), {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify({
      intent,
      candidates,
      stream: true,
      cached: options.cached ?? useCache,
      ...(apiMode ? { mode: apiMode } : {}),
    }),
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
  comparePath?: string,
): Promise<CompareResponse> {
  const path = comparePath || `/metrics/compare?a=${encodeURIComponent(before)}&b=${encodeURIComponent(after)}&cluster=${encodeURIComponent(cluster)}`;
  const response = await fetch(urlFor(path), { headers: { accept: "application/json" } });
  const data = await response.json();
  if (response.status === 202) return { pending: true, data: data as ComparePending };
  if (!response.ok) {
    const message = data?.error?.message || `Comparison failed (${response.status}).`;
    throw new Error(message);
  }
  return { pending: false, data: data as CompareResult };
}
